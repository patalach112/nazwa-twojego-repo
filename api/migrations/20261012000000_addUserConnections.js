/** Account creation and social settings already use this table, but it was missing. */
const columns = ['user_id', 'discord', 'twitter', 'telegram', 'tiktok', 'youtube', 'twitch', 'github', 'roblox', 'created_at', 'updated_at'];
const marker = 'Created by 20261012000000_addUserConnections';

exports.up = async knex => {
    if (await knex.schema.hasTable('user_connections')) {
        for (const name of columns) {
            if (!await knex.schema.hasColumn('user_connections', name)) {
                throw new Error('Existing user_connections needs column ' + name + '; reconcile the legacy schema without deleting its data before retrying.');
            }
        }
        return; // Preserve a complete legacy table, including its ownership and data.
    }
    await knex.schema.createTable('user_connections', table => {
        table.bigInteger('user_id').primary().references('id').inTable('user').onDelete('CASCADE');
        for (const name of columns.slice(1, 9)) table.text(name).nullable();
        table.timestamp('created_at').notNullable().defaultTo(knex.fn.now());
        table.timestamp('updated_at').notNullable().defaultTo(knex.fn.now());
        table.comment(marker);
    });
};

exports.down = async knex => {
    if (!await knex.schema.hasTable('user_connections')) return;
    const result = await knex.raw("SELECT obj_description('public.user_connections'::regclass, 'pg_class') AS comment");
    if (result.rows[0].comment !== marker) return; // Never drop an adopted legacy table.
    const {count} = await knex('user_connections').count('* as count').first();
    if (Number(count) !== 0) throw new Error('Refusing to drop populated user_connections; preserve or export the account social data first.');
    await knex.schema.dropTable('user_connections');
};
