# lmp_visualizer/panel_protocol.py

"""Shared contract for the mode panels (log / trj / dsd).

The panels are duck-typed, not subclassed from a common base: MainWindow
discovers the contract at call time. This module declares that contract once,
as a ``typing.Protocol``, so MainWindow can route through plain
``hasattr``/``getattr`` checks instead of branching on ``inspect.signature``.

Panels satisfy the Protocol implicitly - no inheritance is required and none
is forced. A panel only needs the five methods with compatible signatures.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class PanelProtocol(Protocol):
    """Structural contract every mode panel fulfils."""

    def load_project(self, root_path, keywords, force_reload=False,
                     keep_table=False, target_system=None):
        """(Re)discover data for the given project path(s) and refresh the panel."""
        ...

    def save_session_to_file(self, path):
        """Persist the panel's session state to a JSON file."""
        ...

    def load_session_from_file(self, path):
        """Restore the panel's session state from a JSON file."""
        ...

    def attach_mode_combo(self, combo_box):
        """Reparent the floating mode selector into this panel's layout."""
        ...

    def _update_ui_state(self, project_loaded, keep_table=False):
        """Enable/disable controls; optionally keep the row table as-is."""
        ...


#: The contract members MainWindow may probe for, in declaration order.
PANEL_METHODS = (
    'load_project',
    'save_session_to_file',
    'load_session_from_file',
    'attach_mode_combo',
    '_update_ui_state',
)

#: Routing order for load_project kwargs. Narrower duck-typed panels are
#: retried with trailing entries dropped first (see call_load_project).
LOAD_KWARG_ORDER = ('force_reload', 'keep_table', 'target_system')


def has_panel_method(panel, name: str) -> bool:
    """hasattr/getattr-style probe for one contract member."""
    return callable(getattr(panel, name, None))


def conforms_to_panel_protocol(panel) -> dict:
    """Per-method conformance of a panel; all True for the shipped panels."""
    return {name: has_panel_method(panel, name) for name in PANEL_METHODS}


def is_panel(panel) -> bool:
    """True when the object satisfies the whole PanelProtocol contract."""
    return all(has_panel_method(panel, name) for name in PANEL_METHODS)


def call_load_project(panel, path, keywords, force_reload=False,
                      keep_table=False, target_system=None) -> bool:
    """Route a load to a duck-typed panel, preserving the old kwarg routing.

    Every shipped panel accepts all three kwargs, so the first call succeeds
    exactly as the former ``inspect.signature`` branching did. A panel with a
    narrower signature is retried with the rejected trailing kwarg dropped
    (target_system, then keep_table, then force_reload). Any other TypeError
    is a real failure inside the loader and is re-raised, never masked.
    Returns False when the object exposes no load_project at all.
    """
    loader = getattr(panel, 'load_project', None)
    if not callable(loader):
        return False
    kwargs = {
        'force_reload': force_reload,
        'keep_table': keep_table,
        'target_system': target_system,
    }
    while True:
        try:
            loader(path, keywords, **kwargs)
            return True
        except TypeError as exc:
            if not kwargs or 'unexpected keyword argument' not in str(exc):
                raise
            kwargs.popitem()
