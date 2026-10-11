from scripts.build_learning_installer import preview_credential_hook
from refactor.desktop.delivery.install_product import Product


def test_preview_compiles_distinct_locker_service(tmp_path, monkeypatch):
    hooks = preview_credential_hook(tmp_path, Product('MentorFlow.UAT.setup102c'))
    from pathlib import Path
    source = Path(hooks[0]).read_text(encoding='utf-8')
    from refactor.desktop import credential_store
    monkeypatch.setattr(credential_store, 'SERVICE_NAME', 'production-test-sentinel')
    exec(compile(source, hooks[0], 'exec'), {})
    assert credential_store.SERVICE_NAME == 'MentorFlow.UAT.setup102c.Provider.v1'


def test_production_does_not_install_preview_hook(tmp_path):
    assert preview_credential_hook(tmp_path, Product()) == []
    assert list(tmp_path.iterdir()) == []
