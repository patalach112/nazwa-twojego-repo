/**
 * GameServerService already queries these fields, but the source's migrations
 * never created them. Do not silently delete conflicting legacy memberships:
 * an operator must reconcile them before adding the uniqueness constraints.
 * @param {import('knex')} knex
 */
exports.up = async (knex) => {
    const duplicateJobs = await knex('asset_server').select('id').groupBy('id').havingRaw('COUNT(*) > 1').first();
    const duplicatePlayers = await knex('asset_server_player').select('user_id').groupBy('user_id').havingRaw('COUNT(*) > 1').first();
    if (duplicateJobs || duplicatePlayers) {
        throw new Error('Reconcile duplicate asset_server job IDs and asset_server_player user memberships before applying game-server reporting constraints. No rows have been deleted.');
    }
    const orphan = await knex('asset_server_player as p').leftJoin('asset_server as s', 's.id', 'p.server_id').whereNull('s.id').first();
    if (orphan) {
        throw new Error('Reconcile orphaned asset_server_player memberships before applying game-server reporting constraints. No rows have been deleted.');
    }

    await knex.schema.alterTable('asset_server', (t) => {
        t.primary(['id']);
        t.integer('status').notNullable().defaultTo(1); // Loading = 1, Ready = 2, Closing = 3
        t.integer('type').notNullable().defaultTo(1); // matchmakingContextId
        t.bigInteger('ping').notNullable().defaultTo(0);
        t.bigInteger('fps').notNullable().defaultTo(0);
    });
    await knex.schema.alterTable('asset_server_player', (t) => {
        t.unique(['user_id']); // one active game per player, not one row per retry
        t.foreign('server_id').references('id').inTable('asset_server').onDelete('CASCADE');
    });
    await knex.schema.alterTable('asset_play_history', (t) => {
        // History outlives a server. Keep its job ID rather than cascading it away.
        t.uuid('server_id').nullable();
        t.index(['server_id', 'user_id', 'ended_at']);
    });
};

exports.down = async (knex) => {
    await knex.schema.alterTable('asset_play_history', (t) => {
        t.dropIndex(['server_id', 'user_id', 'ended_at']);
        t.dropColumn('server_id');
    });
    await knex.schema.alterTable('asset_server_player', (t) => {
        t.dropForeign('server_id');
        t.dropUnique(['user_id']);
    });
    await knex.schema.alterTable('asset_server', (t) => {
        t.dropPrimary();
        t.dropColumns('status', 'type', 'ping', 'fps');
    });
};
