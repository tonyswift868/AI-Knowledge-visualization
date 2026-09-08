"""Build-provisioned product namespace, not a command-line/env override.

UAT uses the same installer/update engine with distinct registration and data
names. Production builds do not import/trust a downloaded test configuration.
"""
from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class Product:
    app_id: str = 'MentorFlow'

    def __post_init__(self):
        if re.fullmatch(r'MentorFlow(?:\.UAT\.[A-Za-z0-9-]{1,32})?',self.app_id) is None:
            raise ValueError('invalid compiled product namespace')

    @property
    def uat(self): return self.app_id != 'MentorFlow'
    @property
    def product_key(self): return 'Software\\' + self.app_id
    @property
    def uninstall_key(self): return 'Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\' + self.app_id
    @property
    def shortcut_name(self): return ('MentorFlow 预览版' if self.uat else 'MentorFlow') + '.lnk'
    @property
    def registry_fields(self):
        return {self.product_key: ('InstallLocation','Version'), self.uninstall_key:
            ('DisplayName','DisplayVersion','Publisher','InstallLocation','UninstallString','NoModify','NoRepair')}

    def validate_root(self,root):
        root=Path(root).absolute()
        if self.uat:
            for forbidden in map(Path,('E:/MentorFlow','E:/MentorFlowData')):
                if root==forbidden or root.is_relative_to(forbidden) or forbidden.is_relative_to(root):
                    raise ValueError('UAT cannot target formal or historical installation roots')
        return root


def configured():
    # This is generated into the frozen PYZ only. Never look beside the EXE,
    # at a registry key, environment variable, feed, or user configuration.
    try:
        from mentorflow_delivery_authority import APP_ID
    except ModuleNotFoundError as error:
        if error.name!='mentorflow_delivery_authority': raise
        return Product()
    return Product(APP_ID)
