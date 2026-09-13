"""Chinese two-device view; no sync producer and no acceptance mutation."""
from PySide6.QtWidgets import QComboBox, QFrame, QVBoxLayout, QPushButton, QScrollArea, QWidget
from .authority import public_text
from .devices import Devices, SCENARIOS
from .presentation import short_path


class DevicePanel(QScrollArea):
    def __init__(self, workbench, label_factory, devices=None):
        super().__init__()
        self.workbench, self.devices = workbench, devices or Devices()
        self.setWidgetResizable(True)
        content = QWidget(); self.setWidget(content)
        body = QVBoxLayout(content); body.setContentsMargins(12, 12, 12, 12)
        self.text = lambda key, **values: workbench.tr_text('devices', key, **values)
        body.addWidget(label_factory(self.text('title'), 23, True))
        self.scenario_box = QComboBox()
        for key, scenario in zip(('cloud','missing','portable'), SCENARIOS):
            self.scenario_box.addItem(self.text(key), scenario)
        self.scenario_box.model().item(2).setEnabled(False)
        body.addWidget(self.scenario_box)
        refresh = QPushButton(self.text('refresh')); refresh.clicked.connect(workbench.refresh); body.addWidget(refresh)
        self.scenario = label_factory(); body.addWidget(self.scenario)
        body.addWidget(label_factory(self.text('portable')))
        body.addWidget(label_factory(self.text('scope'), 13))
        self.rows = {}
        for device in ('A', 'B'):
            card = QFrame(); card.setObjectName('card'); layout = QVBoxLayout(card)
            layout.addWidget(label_factory(self.text(device), 18, True))
            summary = label_factory(); layout.addWidget(summary)
            launch = QPushButton(self.text('launch', device=device))
            launch.setObjectName('primary'); launch.clicked.connect(lambda checked=False, d=device: self.operate(d, False))
            reset = QPushButton(self.text('reset', device=device)); reset.clicked.connect(lambda checked=False, d=device: self.operate(d, True))
            details = QPushButton(self.text('details')); details.clicked.connect(lambda checked=False, d=device: self.details(d))
            for button in (launch, reset, details): layout.addWidget(button)
            self.rows[device] = dict(summary=summary, launch=launch, reset=reset, details=details)
            body.addWidget(card)
        self.notice = label_factory(); body.addWidget(self.notice); body.addStretch()
        self.show_view({})
        self.scenario_box.currentIndexChanged.connect(lambda _: self.show_view(self.view))

    def show_view(self, view):
        self.view = view
        state = self.text('waiting_observation' if view.get('server_sync_state') == 'IMPLEMENTED' else 'waiting_sync')
        self.scenario.setText(self.text('cloud') + ' / ' + self.text('missing') + '：' + state)
        active = view.get('active_target')
        ready = bool(view.get('device_profile_supported')) and self.devices.compatible(active, view.get('integration_sha'), self.workbench.paths)
        for device, row in self.rows.items():
            values, owned = {}, False
            running = self.devices.root(device).exists()
            try:
                observed = self.devices.observation(device, active, self.scenario_box.currentData())
                values, owned = observed['values'], True
                running = self.devices.busy(device)
            except (ValueError, OSError, RuntimeError): pass
            unknown = self.text('unknown')
            account = public_text(values['account']) if values.get('account') else unknown
            if len(account) > 32: account = account[:24] + '…'
            row['summary'].setText('\n'.join((
                self.text('account', value=account),
                self.text('root', value=short_path(str(self.devices.root(device))) if owned else self.text('unowned' if running else 'unprepared')),
                self.text('workspace', value=self.text('observed') if values.get('server_workspace') else unknown),
                self.text('revision', value=self.text('observed') if values.get('last_sync_revision') else unknown),
                self.text('restored', value=values.get('cloud_entities_restored_count', unknown)),
                self.text('missing_count', value=values.get('missing_local_resources_count', unknown)),
                self.text('build_bound' if ready else 'build_wait'))))
            row['launch'].setEnabled(ready and not self.workbench.jobs and not running)
            row['reset'].setEnabled(owned and not self.workbench.jobs and not running)

    def details(self, device):
        try: value = self.devices.observation(device, self.view.get('active_target'), self.scenario_box.currentData())
        except (ValueError, OSError, RuntimeError): value = {'root': str(self.devices.root(device)), 'status': 'NOT_PREPARED'}
        self.workbench.open_details(self.text(device), value)

    def operate(self, device, reset):
        if self.workbench.jobs: return
        scenario = self.scenario_box.currentData()
        for row in self.rows.values():
            row['launch'].setEnabled(False); row['reset'].setEnabled(False)
        def operation():
            try:
                if reset: return self.devices.reset(device)
                return self.devices.launch(device, self.view.get('active_target'), self.workbench.source_factory(),
                                           self.workbench.paths, cancelled=self.workbench.cancel.is_set, scenario=scenario)
            except (ValueError, OSError):
                from .targets import Refused
                raise Refused('TEST_DEVICE_UNSAFE_PATH') from None
        def done(_):
            self.notice.setText(self.text('reset_done' if reset else 'launch_done'))
        self.workbench.cancel.clear()
        self.workbench.start_job(operation, done)
