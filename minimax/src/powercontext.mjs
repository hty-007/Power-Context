const family = 'atomic-memory';

export function powerContext(base = process.env.SDD_PC_URL, token = process.env.SDD_PC_TOKEN) {
  if (!base || !token) throw new Error('Set SDD_PC_URL and SDD_PC_TOKEN for the local PowerContext server.');
  const url = new URL(base);
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
    || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('SDD_PC_URL must be a loopback HTTP origin.');
  }
  const audit = [];

  async function request(method, path, body, etag) {
    const headers = { Accept: 'application/json', Authorization: `Bearer ${token}` };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (etag) headers['If-Match'] = etag;
    const response = await fetch(url.origin + path, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(10_000), redirect: 'error',
    });
    audit.push({ method, path, status: response.status, requestId: response.headers.get('x-powercontext-request-id') });
    if (!response.ok) throw new Error(`PowerContext ${method} ${path}: HTTP ${response.status}`);
    return { data: await response.json(), etag: response.headers.get('etag') };
  }

  async function openTask(taskId) {
    const { data } = await request('POST', '/v1/scopes', {
      title: '工单列表 · SDD 验收',
      summary: '通过 Atomic Memory 保存工单列表开发的检查结果和待办事项。',
      idempotency_key: 'sdd-issue-list-demo-v1',
    });
    const scope = data.scope_id;
    const kind = `sdd-issue-list-${taskId}`;
    const artifacts = `/v1/scopes/${encodeURIComponent(scope)}/artifacts`;

    async function load() {
      const { data } = await request('POST', '/v1/atomic-memory/list', {
        scope_id: scope, kind, states: ['active'], limit: 2,
      });
      if (data.items.length > 1 || data.next_cursor) throw new Error('More than one active state for this task.');
      const item = data.items[0];
      return item ? { artifact: item.artifact, state: JSON.parse(item.text) } : null;
    }

    async function save(state, previous) {
      const content = { kind, text: JSON.stringify(state) };
      if (!previous) {
        const { data } = await request('POST', artifacts, { family, content });
        return data;
      }
      const path = `${artifacts}/${family}/${encodeURIComponent(previous.artifact.artifact_id)}`;
      const { data, etag } = await request('GET', path);
      if (!etag || data.revision !== previous.artifact.revision) throw new Error('Task changed. Reload its context.');
      const { data: updated } = await request('PUT', path, { content }, etag);
      return updated;
    }

    return { scope, load, save };
  }

  return { request, openTask, audit };
}
