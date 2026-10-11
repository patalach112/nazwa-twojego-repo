/** @param {import('knex')} knex */
exports.up = async (knex) => {
    const conflict = await knex('asset_server').select('ip', 'port').groupBy('ip', 'port').havingRaw('COUNT(*) > 1').first();
    if (conflict) throw new Error('Reconcile duplicate game-server network ports on the same host before applying port reservations; no rows were deleted');
    await knex.schema.alterTable('asset_server', t => {
        t.integer('rcc_port').nullable();
        t.integer('proxy_port').nullable();
        t.unique(['ip', 'port'], 'asset_server_network_port_unique');
        t.unique(['ip', 'rcc_port'], 'asset_server_rcc_port_unique');
        t.unique(['ip', 'proxy_port'], 'asset_server_proxy_port_unique');
    });
};

/** @param {import('knex')} knex */
exports.down = async (knex) => {
    await knex.schema.alterTable('asset_server', t => {
        t.dropUnique(['ip', 'port'], 'asset_server_network_port_unique');
        t.dropUnique(['ip', 'rcc_port'], 'asset_server_rcc_port_unique');
        t.dropUnique(['ip', 'proxy_port'], 'asset_server_proxy_port_unique');
        t.dropColumn('rcc_port');
        t.dropColumn('proxy_port');
    });
};
