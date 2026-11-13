import { createConnection, createServer } from 'node:net';
import { seekdb } from '@oceanbase/seekdb-node';

// seekdb-js speaks MySQL over TCP. The native POSIX runtime supplies a Unix socket.
async function localSocketBridge(path) {
  const server = createServer(client => {
    const upstream = createConnection({ path });
    client.on('error', () => upstream.destroy());
    upstream.on('error', () => client.destroy());
    client.on('close', () => upstream.destroy());
    upstream.on('close', () => client.destroy());
    client.pipe(upstream).pipe(client);
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  return server;
}

export async function openLocalDatabase(directory) {
  const database = await seekdb.open(directory, { parameters: { memory_budget: '2G', mysql_port_mode: 'random' } });
  let bridge;
  try {
    const endpoint = database.connectionOptions();
    if (endpoint.transport !== 'tcp') {
      bridge = await localSocketBridge(endpoint.unixSocket ?? endpoint.namedPipe);
    }
    return {
      options: {
        host: bridge ? '127.0.0.1' : endpoint.host,
        port: bridge ? bridge.address().port : endpoint.port,
        user: endpoint.user,
        database: 'test',
        password: '',
      },
      close() {
        bridge?.close();
        database.close();
      },
    };
  } catch (error) {
    bridge?.close();
    database.close();
    throw error;
  }
}
