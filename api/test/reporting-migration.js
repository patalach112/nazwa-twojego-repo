'use strict';

const assert = require('node:assert/strict');
const path = require('node:path');
const { parse } = require('pg-connection-string');

if (!process.env.TEST_DATABASE_URL) throw new Error('TEST_DATABASE_URL must point to an isolated empty *_tests database');
const connection = parse(process.env.TEST_DATABASE_URL);
if (!connection.database.endsWith('_tests')) throw new Error('Refusing to test migrations against a database not ending in _tests');
const knex = require('knex')({ client: 'pg', connection, migrations: { directory: path.resolve(__dirname, '../migrations') } });
const migrationName = '20261011000000_addGameServerReportingState.js';
const migration = require('../migrations/' + migrationName);
const id = '11111111-1111-4111-8111-111111111111';
const second = '22222222-2222-4222-8222-222222222222';
const server = jobId => ({ id: jobId, asset_id: 1, ip: '127.0.0.1', port: jobId === id ? 51000 : 51001, server_connection: '127.0.0.1:51000' });

(async () => {
    await knex.migrate.latest();
    assert.equal(Number((await knex('knex_migrations').count('* as count').first()).count), 89);
    assert.equal(Number((await knex('asset_server').count('* as count').first()).count), 0, 'Use an empty test database');
    const tables = await knex('information_schema.tables').where({ table_schema: 'public', table_type: 'BASE TABLE' }).whereNotIn('table_name', ['knex_migrations', 'knex_migrations_lock']).count('* as count').first();
    console.log(`All 89 migrations passed; ${tables.count} application tables.`);
    const columns = await knex('information_schema.columns').where({ table_schema: 'public', table_name: 'asset_server' }).select('column_name');
    for (const name of ['status', 'type', 'ping', 'fps']) assert(columns.some(c => c.column_name === name));

    await knex('asset_server').insert(server(id));
    const created = await knex('asset_server').where({ id }).first();
    assert.equal(created.status, 1);
    assert.equal(created.type, 1);
    await knex('asset_server_player').insert({ server_id: id, user_id: 1, asset_id: 1 });
    await assert.rejects(knex('asset_server_player').insert({ server_id: id, user_id: 1, asset_id: 1 }), /unique/);
    await assert.rejects(knex('asset_server').insert(server(id)), /unique/);
    await knex('asset_server').where({ id }).del();
    assert.equal(Number((await knex('asset_server_player').count('* as count').first()).count), 0);

    await knex.migrate.down({ name: migrationName });
    await knex('asset_server').insert([server(id), server(second)]);
    await knex('asset_server_player').insert([{ server_id: id, user_id: 1, asset_id: 1 }, { server_id: second, user_id: 1, asset_id: 1 }]);
    await assert.rejects(knex.transaction(trx => migration.up(trx)), /Reconcile duplicate/);
    assert.equal(Number((await knex('asset_server_player').count('* as count').first()).count), 2, 'Conflicts must not be silently deleted');
    await knex('asset_server_player').del();
    await knex('asset_server').del();
    await knex('asset_server').insert([server(id), server(id)]);
    await assert.rejects(knex.transaction(trx => migration.up(trx)), /Reconcile duplicate/);
    await knex('asset_server').del();
    await knex('asset_server_player').insert({ server_id: id, user_id: 1, asset_id: 1 });
    await assert.rejects(knex.transaction(trx => migration.up(trx)), /Reconcile orphaned/);
    await knex('asset_server_player').del();
    await knex.migrate.latest();
    console.log('Reporting migration upgrade/rollback, uniqueness, cascade and safe conflict handling passed.');
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => knex.destroy());
