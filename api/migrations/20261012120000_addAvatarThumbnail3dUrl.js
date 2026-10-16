/**
 * The website writes and reads user_avatar.thumbnail_3d_url
 * (Roblox.Services Users/Avatar.cs, Thumbnails.cs), but the table this repository
 * creates never had that column. The first avatar redraw therefore failed on its own
 * update - 42703: column "thumbnail_3d_url" of relation "user_avatar" does not exist -
 * before it could ask a renderer for anything, so every local avatar stayed pending.
 */
const column = 'thumbnail_3d_url';

exports.up = async knex => {
    if (!await knex.schema.hasTable('user_avatar')) {
        throw new Error('user_avatar is missing; run the earlier migrations before this one.');
    }
    if (await knex.schema.hasColumn('user_avatar', column)) return; // A legacy schema may already carry it.
    await knex.schema.alterTable('user_avatar', table => {
        table.text(column).nullable();
    });
};

exports.down = async knex => {
    if (!await knex.schema.hasTable('user_avatar')) return;
    if (!await knex.schema.hasColumn('user_avatar', column)) return;
    const {count} = await knex('user_avatar').whereNotNull(column).count('* as count').first();
    if (Number(count) !== 0) {
        throw new Error('Refusing to drop ' + column + ': ' + count + ' accounts still reference a stored 3D render.');
    }
    await knex.schema.alterTable('user_avatar', table => {
        table.dropColumn(column);
    });
};
