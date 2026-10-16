"""
This is Omega internal API which is built on the top of the plugins API.
Most of the functions here calls corresponding functions of the registered
plugins.
"""

import pathlib
import yaml
import importlib
import importlib.util
import sys
import logging

logger = logging.getLogger(__name__)

_LOADERS = ["python", "metta"]
_REPO = pathlib.Path(__file__).parent.parent.resolve()
_plugins = {}

def _error(func, text):
    error = f"{func}: {text}"
    logger.error(error)
    raise RuntimeError(error)

def listPlugins():
    """Loads the list of the plugins from ./config/plugins.yaml file, applies
    the drop-in files from ./config/plugins.d/ on top of it and then returns
    list of triples (loader, name, location). YAML file contains the list of
    plugins each specified by these fields:
    - name - required, must be unique within one file
    - loader - required, must be either "python" or "metta"
    - location - optional, path to the plugin module used by Python loader
    - enabled - optional, false drops the plugin from the list

    Drop-in files are read in file name order. An entry whose name is already
    listed replaces that entry in place, so a deployment can swap a plugin or
    turn it off without editing plugins.yaml:

        # config/plugins.d/telegram.yaml
        - name: telegram
          loader: metta
          location: "{REPO}/plugins/telegram"
    """
    global _REPO
    return _list_plugins(_REPO.joinpath("config"))

def _list_plugins(config_dir):
    """Merge plugins.yaml and plugins.d/*.yaml from config_dir into the list
    of (loader, name, location) triples."""
    manifests = [config_dir.joinpath("plugins.yaml")]
    manifests += sorted(config_dir.joinpath("plugins.d").glob("*.yaml"))

    # A dict keeps the first position of a key, so a replaced entry loads
    # where the original did.
    entries = {}
    for path in manifests:
        for name, entry in _read_manifest(path):
            entries[name] = entry

    return [_plugin_triple(entry) for entry in entries.values()
            if entry.get("enabled", True) is not False]

def _read_manifest(path):
    """Return the (name, entry) pairs of one manifest file in file order."""
    with open(path, "r") as f:
        yaml_content = yaml.safe_load(f) or []

    names = set()
    for i, p in enumerate(yaml_content):
        name = p.get("name")
        if name is None:
            _error("listPlugins", f"name field is empty, file: {path}, index: {i}")
        if name in names:
            _error("listPlugins", f"name '{name}' is not unique, file: {path}")
        names.add(name)
        yield name, p

def _plugin_triple(p):
    """Validate one manifest entry and turn it into (loader, name, location)."""
    global _REPO, _LOADERS

    loader = p.get("loader", "metta")
    if not loader in _LOADERS:
        _error("listPlugins", f"incorrect loader type '{loader}', only {_LOADERS} are supported")

    location = p.get("location")
    if location is not None:
        location = str(pathlib.Path(location.format(REPO=_REPO)).resolve())
    else:
        location = ""

    return [loader, p["name"], location]

class PythonPlugin:
    """Class to wrap the reference to the Python module and keep it inside
    plugins registry"""

    def __init__(self, mod):
        self.mod = mod

def addLocationToPath(location):
    """Add a plugin's folder to sys.path so a plugin made of several files can
    import its own modules. Used by the Python loader for multi-file Python
    plugins, and by the MeTTa loader so a MeTTa plugin can py-call into its own
    .py file."""
    global _REPO
    sys.path.append(str(pathlib.Path(location.format(REPO=_REPO)).resolve()))

def loadPythonPlugin(name, location):
    """Python plugin loader implementation. If location of the plugin is
    specified it imports "<location>/<name>.py" file. Imports <name> Python
    module otherwise. Calls "loadOmegaPlugin" function from the imported
    module. This is the point where plugin's code gets control and should
    register appropriate callbacks. Repeated loads of the same module do not
    call the entry point again."""
    global _plugins, _REPO

    mod = None
    if not location:
        logger.info(f"_initPythonPlugin: loading {name} plugin from PYTHONPATH using Python module loader")
        mod = importlib.import_module(name)
    else:
        location = pathlib.Path(location.format(REPO=_REPO)).resolve()
        modpath = location.joinpath(f"{name}.py").resolve()
        logger.info(f"_initPythonPlugin: loading {name} plugin from {modpath} using Python module loader")
        if name in sys.modules:
            mod = sys.modules[name]
            existing_path = getattr(mod, "__file__", None)
            if existing_path is None or pathlib.Path(existing_path).resolve() != modpath:
                _error("_initPythonPlugin", f"Plugin name '{name}' is already used by another module ({existing_path}); cannot load {modpath}")
        else:
            # Also protect dependencies that are importable but not loaded yet.
            existing_spec = importlib.util.find_spec(name)
            if existing_spec is not None:
                existing_path = existing_spec.origin
                if existing_path is None or existing_path in ("built-in", "frozen") or pathlib.Path(existing_path).resolve() != modpath:
                    _error("_initPythonPlugin", f"Plugin name '{name}' is already used by another importable module ({existing_path}); cannot load {modpath}; choose a different plugin name")

        # Multi-file plugins need their folder available for sibling imports.
        if str(location) not in sys.path:
            addLocationToPath(str(location))
        if mod is None:
            spec = importlib.util.spec_from_file_location(name, modpath)
            mod = importlib.util.module_from_spec(spec)
            # Match normal import semantics, including imports during execution.
            sys.modules[name] = mod
            try:
                spec.loader.exec_module(mod)
            except BaseException:
                if sys.modules.get(name) is mod:
                    del sys.modules[name]
                raise

    if mod is None:
        _error("_initPythonPlugin", f"Couldn't find Python module {name}")

    if name in _plugins:
        if _plugins[name].mod is mod:
            return
        _error("_initPythonPlugin", f"Plugin name '{name}' is already registered with another module")

    if not hasattr(mod, "loadOmegaPlugin"):
        _error("_initPythonPlugin", f"No loadOmegaPlugin() function is implemented by plugin {name}")
    plugin_loader = getattr(mod, "loadOmegaPlugin")
    _plugins[name] = PythonPlugin(mod)
    try:
        plugin_loader()
    except BaseException:
        del _plugins[name]
        raise
