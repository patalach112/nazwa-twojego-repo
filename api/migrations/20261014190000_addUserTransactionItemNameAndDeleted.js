/**
 * Add item_name and deleted columns to user_transaction.
 * Economy and inventory operations reference these columns
 * (e.g. Economy.cs InsertTransaction, Inventory.cs MarkTransactionAsDeleted,
 * and Users.cs HasUserPurchasedAssetBefore), but the original schema lacked them.
 * Without these columns, PostgreSQL threw error 42703 (undefined column) on any item purchase.
 */
exports.up = async (knex) => {
    if (!await knex.schema.hasTable('user_transaction')) {
        throw new Error('user_transaction is missing; run earlier migrations first.');
    }
    const hasItemName = await knex.schema.hasColumn('user_transaction', 'item_name');
    const hasDeleted = await knex.schema.hasColumn('user_transaction', 'deleted');
    await knex.schema.alterTable('user_transaction', (t) => {
        if (!hasItemName) t.string('item_name', 255).nullable().defaultTo(null);
        if (!hasDeleted) t.boolean('deleted').notNullable().defaultTo(false);
    });
};

exports.down = async (knex) => {
    if (!await knex.schema.hasTable('user_transaction')) return;
    const hasItemName = await knex.schema.hasColumn('user_transaction', 'item_name');
    const hasDeleted = await knex.schema.hasColumn('user_transaction', 'deleted');
    await knex.schema.alterTable('user_transaction', (t) => {
        if (hasItemName) t.dropColumn('item_name');
        if (hasDeleted) t.dropColumn('deleted');
    });
};
