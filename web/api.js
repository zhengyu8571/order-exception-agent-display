export function createApi(getToken) {
  return async function api(path, { method = 'GET', body } = {}) {
    const response = await fetch('/api' + path, {
      method,
      headers: { Authorization: 'Bearer ' + getToken(), 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let result;
    try {
      result = await response.json();
    } catch {
      throw new Error('服务响应暂不可用，请稍后恢复任务');
    }
    if (!response.ok)
      throw new Error(
        typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail),
      );
    return result;
  };
}
