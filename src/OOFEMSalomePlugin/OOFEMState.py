"""Persistent, study-local state for the OOFEM plugin."""

import json
import logging
from pathlib import Path


_logger = logging.getLogger(__name__)

COMPONENT_TYPE = "OOFEM"
COMPONENT_NAME = "OOFEM"
STATE_OBJECT_NAME = "Plugin configuration"
STATE_ATTRIBUTE = "AttributeString"

# Retained only for compatibility with old tests or embeddings that supplied
# dictionary-like GetString/SetString methods. SALOME's CORBA Study proxy
# advertises methods with these names, but the server does not implement them
# as legal Study operations in current SALOME releases.
ATTR_NAME = "OOFEM_OOFEMState_v1"

# Native SALOME light modules persist their data through the saveFiles/openFiles
# callbacks. SALOME places each returned file in the study HDF container.
STATE_FILE_NAME = "oofem-study.json"
STATE_FILE_FORMAT = "OOFEM SALOME project"
STATE_FILE_VERSION = 1


def _decode(raw):
    if not raw:
        return {}
    try:
        state = json.loads(raw)
    except (TypeError, ValueError):
        _logger.warning("Ignoring invalid OOFEM state stored in the study")
        return {}
    if not isinstance(state, dict):
        _logger.warning("Ignoring OOFEM study state that is not an object")
        return {}
    return state


def _decode_file_document(raw):
    """Decode a native-module state document.

    A raw dictionary is accepted for early development builds that wrote the
    state without a versioned envelope.
    """
    try:
        document = json.loads(raw)
    except (TypeError, ValueError):
        _logger.warning("Ignoring invalid OOFEM project JSON")
        return None
    if not isinstance(document, dict):
        _logger.warning("Ignoring OOFEM project JSON that is not an object")
        return None
    if "format" not in document and "state" not in document:
        return document
    if document.get("format") != STATE_FILE_FORMAT:
        _logger.warning("Ignoring a file that is not an OOFEM SALOME project")
        return None
    if document.get("version") != STATE_FILE_VERSION:
        _logger.warning(
            "Unsupported OOFEM SALOME project version: %r", document.get("version")
        )
        return None
    state = document.get("state")
    if not isinstance(state, dict):
        _logger.warning("Ignoring OOFEM SALOME project with invalid state")
        return None
    return state


def _find_component(study):
    try:
        return study.FindComponent(COMPONENT_TYPE)
    except Exception:
        _logger.debug("Could not find the OOFEM study component", exc_info=True)
        return None


def _find_state_object(study, component):
    if component is None:
        return None
    try:
        iterator = study.NewChildIterator(component)
        while iterator.More():
            child = iterator.Value()
            if child.GetName() == STATE_OBJECT_NAME:
                return child
            iterator.Next()
    except Exception:
        _logger.debug("Could not enumerate the OOFEM study component", exc_info=True)
    return None


def _attribute_from_result(result):
    """Normalize SALOME's ``(found, attribute)`` result across wrappers."""
    if isinstance(result, tuple):
        return result[1] if result and result[0] else None
    return result


def _uses_standard_salome_storage(study):
    """Return whether *study* exposes SALOME's builder-based storage API."""
    return callable(getattr(study, "NewBuilder", None))



class OOFEMState:
    @staticmethod
    def load_file(filename):
        """Load a versioned state document used by SALOME openFiles.

        None indicates an unreadable or invalid file; an empty dictionary is
        a valid new project.
        """
        try:
            raw = Path(filename).read_text(encoding="utf-8")
        except OSError:
            _logger.warning(
                "Could not read OOFEM project file %s", filename, exc_info=True
            )
            return None
        return _decode_file_document(raw)

    @staticmethod
    def save_file(filename, state_dict):
        """Write the state document returned by SALOME saveFiles."""
        if not isinstance(state_dict, dict):
            return False
        document = {
            "format": STATE_FILE_FORMAT,
            "version": STATE_FILE_VERSION,
            "state": state_dict,
        }
        try:
            payload = json.dumps(document, indent=2, sort_keys=True)
            Path(filename).write_text(payload + "\n", encoding="utf-8")
        except (OSError, TypeError, ValueError):
            _logger.warning(
                "Could not write OOFEM project file %s", filename, exc_info=True
            )
            return False
        return True

    @staticmethod
    def load(study):
        """Return the persisted plugin state, or an empty dictionary."""
        if study is None:
            return {}

        component = _find_component(study)
        state_object = _find_state_object(study, component)
        if state_object is not None:
            try:
                builder = study.NewBuilder()
                attribute = _attribute_from_result(
                    builder.FindAttribute(state_object, STATE_ATTRIBUTE)
                )
                if attribute is not None:
                    return _decode(attribute.Value())
            except Exception:
                _logger.warning("Could not load OOFEM state from the study", exc_info=True)
                return {}

        # Compatibility only for the initial prototype and lightweight
        # embedders. Never call GetString on a real CORBA Study proxy: it is
        # present in the generated stub but raises CORBA.UNKNOWN server-side.
        if not _uses_standard_salome_storage(study) and hasattr(study, "GetString"):
            try:
                return _decode(study.GetString(ATTR_NAME))
            except Exception:
                _logger.debug("Legacy OOFEM state could not be read", exc_info=True)
        return {}

    @staticmethod
    def save(study, state_dict):
        """Persist state on an OOFEM study component using AttributeString."""
        if study is None or not isinstance(state_dict, dict):
            return False

        payload = json.dumps(state_dict, sort_keys=True, separators=(",", ":"))
        try:
            builder = study.NewBuilder()
            component = _find_component(study)
            if component is None:
                component = builder.NewComponent(COMPONENT_TYPE)
                builder.SetName(component, COMPONENT_NAME)

            state_object = _find_state_object(study, component)
            if state_object is None:
                state_object = builder.NewObject(component)
                builder.SetName(state_object, STATE_OBJECT_NAME)

            attribute = builder.FindOrCreateAttribute(state_object, STATE_ATTRIBUTE)
            attribute.SetValue(payload)
            return True
        except Exception:
            _logger.debug("Standard SALOME state storage is unavailable", exc_info=True)

        # Compatibility fallback for the original prototype/test doubles.
        if not _uses_standard_salome_storage(study) and hasattr(study, "SetString"):
            try:
                study.SetString(ATTR_NAME, payload)
                return True
            except Exception:
                _logger.warning("Could not persist legacy OOFEM state", exc_info=True)
        return False
