"""YJ-64 standalone internal diagnostic agent UI."""

from __future__ import annotations

import json
from pathlib import Path

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label


class DiagnosticAgentApp(App):
    """Small control surface for the internal diagnostic agent."""

    def build(self):
        self.status = Label(
            text="Internal diagnostic agent: starting...",
            halign="left",
            valign="top",
        )
        self.status.bind(
            size=lambda instance, value: setattr(instance, "text_size", value)
        )
        root = BoxLayout(orientation="vertical", padding=16, spacing=12)
        root.add_widget(
            Label(
                text="YJ-64 Internal Diagnostic Agent",
                size_hint_y=None,
                height=70,
            )
        )
        root.add_widget(self.status)
        Clock.schedule_once(self._start_service, 0.5)
        Clock.schedule_interval(self._refresh, 1.0)
        return root

    def _start_service(self, *_):
        try:
            from jnius import autoclass

            service_class = autoclass("org.blackmirror.yj64internal.ServiceInternal")
            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            service_class.start(activity, "")
            self.status.text = (
                "Internal agent started. Running self-diagnostic and bridge handshake..."
            )
        except Exception as exc:
            self.status.text = (
                f"Internal agent start failed: {type(exc).__name__}: {exc}"
            )

    def _refresh(self, *_):
        report_path = Path(self.user_data_dir) / "diagnostic-agent-status.json"
        if not report_path.exists():
            return
        try:
            data = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.status.text = (
            f"Agent: {data.get('agent', 'unknown')}\n"
            f"Bridge: {data.get('bridge_status', 'unknown')}\n"
            f"Target launch: {data.get('target_launch', 'unknown')}\n"
            f"Last event: {data.get('event', 'unknown')}"
        )


DiagnosticAgentApp().run()
