/**
 * Group transfers use group_id_one/group_id_two instead of a user party. The
 * original group migration added those columns but left both user IDs required.
 * @param {import('knex')} knex
 */
exports.up = async (knex) => {
    await knex.raw('ALTER TABLE user_transaction ALTER COLUMN user_id_one DROP NOT NULL, ALTER COLUMN user_id_two DROP NOT NULL');
};

exports.down = async (knex) => {
    // PostgreSQL refuses this rollback if group-only parties are still present;
    // do not rewrite or delete those transactions to make the rollback succeed.
    await knex.raw('ALTER TABLE user_transaction ALTER COLUMN user_id_one SET NOT NULL, ALTER COLUMN user_id_two SET NOT NULL');
};
