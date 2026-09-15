"""Two account preference families, projected through the local product API.

All other QSettings keys remain device local. No enumeration/export of the
registry, credential references, provider configuration or caches occurs.
"""
class AccountProductSettings:
    def __init__(self, local, api, report):
        self.local, self.api, self.report = local, api, report
        self.values = api.request('GET','/api/product-settings')

    def __getattr__(self, name):
        return getattr(self.local,name)

    @staticmethod
    def mapped(key):
        if key == 'analogy/keywords' or (key.startswith('account-history/v1/') and key.endswith('/analogy/keywords')):
            return 'analogy/keywords', None
        if key.startswith('task-colors/'):
            return 'task-colors', key[len('task-colors/'):]
        return None

    def value(self,key,default=None,*args,**kwargs):
        match = self.mapped(key)
        if not match:
            return self.local.value(key,default,*args,**kwargs)
        name, child = match
        value = self.values.get(name)
        if child:
            return (value or {}).get(child,default)
        return default if value is None else value

    def setValue(self,key,value):
        match = self.mapped(key)
        if not match:
            return self.local.setValue(key,value)
        name, child = match
        data = value
        if child:
            data = dict(self.values.get(name) or {})
            if value is None: data.pop(child,None)
            else: data[child] = value
        # The backend persists before delivery. A failed ACK remains explicitly
        # pending there; retrying this setting uses DesktopApi's same command ID.
        try:
            self.api.request('PUT','/api/product-settings/'+name,json={'value':data})
        except Exception:
            self.report('偏好更改尚未获得云端确认，请恢复连接后重试。')
            return
        self.values[name] = data

    def remove(self,key):
        if self.mapped(key): return self.setValue(key,None)
        return self.local.remove(key)
