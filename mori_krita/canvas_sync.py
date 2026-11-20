"""One-time, restartable migration of legacy local canvases to new cloud copies.

Never writes to a website's default canvas or to an edited cloud document.
The original local JSON remains untouched, including on network failure.
"""
import copy
import hashlib
import json
import threading


class LegacyCanvasMigration:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()

    def run(self, account_key, request, check):
        with self.lock:
            check()
            journal_path = self.store._path(account_key).with_suffix('.cloud-migration.json')
            try:
                journal = json.loads(journal_path.read_text('utf-8'))
            except FileNotFoundError:
                journal = {'version': 1, 'completed': {}}
            if (not isinstance(journal, dict) or journal.get('version') != 1
                    or not isinstance(journal.get('completed'), dict)):
                raise ValueError('旧画布迁移记录无法读取，原资料已保留')
            identifiers = ['default'] + [item['id'] for item in self.store._registry(account_key)['documents']]
            migrated = 0
            for identifier in identifiers:
                check()
                # A fresh installation must not create an empty cloud copy.
                if not self.store._path(self.store._document_key(account_key, identifier)).is_file():
                    continue
                local = self.store.get(account_key, identifier)
                if not local['nodes'] and not local.get('edges'):
                    continue
                content = {key: copy.deepcopy(local[key]) for key in
                           ('name', 'nodes', 'edges', 'viewport', 'appliedCommandIds', 'coverAssetId') if key in local}
                for node in content.get('nodes', []):
                    data = node.get('data') if isinstance(node, dict) else None
                    if isinstance(node, dict) and node.get('type') == 'creator' and isinstance(data, dict):
                        data.pop('conversationId', None)
                        data['creatorStatus'] = 'idle'
                content['name'] = (str(content.get('name') or 'Krita 创作画布')[:105] + ' · Krita 导入')
                fingerprint = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                receipt = hashlib.sha256((account_key + ':' + identifier + ':' + fingerprint).encode()).hexdigest()
                if receipt in journal['completed']:
                    continue
                # Server creation is idempotent even if its first response is lost.
                created = request('POST', '/api/canvases',
                                  {'name': content['name'], 'clientRequestId': 'krita-migrate-' + receipt}, 'default')
                remote_id = created.get('id')
                if not isinstance(remote_id, str) or remote_id == 'default':
                    raise ValueError('服务器返回的迁移画布无效')
                current = request('GET', '/api/state', None, remote_id)
                if current.get('revision') == 0 and not current.get('nodes') and not current.get('edges'):
                    # Exactly one write attempt. On a lost response the next run
                    # sees a positive revision and leaves the accepted copy alone.
                    request('PUT', '/api/state', {**content, 'expectedRevision': 0}, remote_id)
                elif not isinstance(current.get('revision'), int) or current['revision'] < 1:
                    raise ValueError('迁移画布版本无效，未覆盖云端内容')
                check()
                journal['completed'][receipt] = remote_id
                self.store._write(journal_path, journal)
                migrated += 1
            return migrated
