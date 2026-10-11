// referred_by on signup applications
// note: the users table is named "user" in this schema, not "users", and
// nothing else in here uses real FK constraints - user_id columns are just
// unsigned bigints, so this follows the same pattern

exports.up = async (knex) => {
    await knex.schema.alterTable('join_application', (table) => {
        table.bigInteger('referred_by').unsigned().nullable().defaultTo(null);
    });
};

exports.down = async (knex) => {
    await knex.schema.alterTable('join_application', (table) => {
        table.dropColumn('referred_by');
    });
};
