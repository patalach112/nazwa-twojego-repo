// initial totp (2fa) table made by skylerclock

exports.up = async (knex) => {
    await knex.schema.createTable('user_totp', (table) => {
        table.increments('id').primary();
        table.string('secret').notNullable();
        table.bigInteger('user_id').notNullable().unsigned().index();
        table.string('status').notNullable();
        table.timestamps(true, true);
    });
};

exports.down = async (knex) => {
    await knex.schema.dropTableIfExists('user_totp');
};
