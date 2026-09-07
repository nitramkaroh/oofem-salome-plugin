"""Small SALOME lifecycle helpers shared by plugin and module entry points."""

import logging

_logger = logging.getLogger(__name__)


def load_smesh_component(study):
    """Activate the active study's SMESH engine before using mesh CORBA objects.

    SALOME does not necessarily activate SMESH until the Mesh module has been
    opened. AsterStudy performs this same ``FindOrLoadComponent``/``LoadWith``
    sequence from its module activation callback.

    Until this succeeds, the SMESH objects of a freshly opened HDF study exist
    in the study tree by name but ``SObject.GetObject()`` returns None for every
    one of them, so a mesh list built from them comes out empty -- the mesh only
    shows up once something else activates SMESH, such as clicking Mesh or
    Geometry in the object browser.

    Each step reports separately: a caller that only sees "it did not work"
    cannot tell a study with no mesh in it from an engine that failed to load.
    """
    if study is None:
        _logger.debug("SMESH load skipped: no study")
        return None

    import salome

    # Import the SMESH IDL stubs before anything calls SObject.GetObject().
    # Without them omniORB hands back an object that does not expose
    # GetGroups/GetNodesId, so a mesh list filtered on those attributes comes
    # out empty even though the meshes are loaded and live.  Activating
    # SALOME's Mesh module imports these as a side effect, which is why the
    # meshes used to appear only after clicking Mesh or Geometry in the object
    # browser.
    try:
        import SMESH  # noqa: F401
    except ImportError:
        _logger.warning(
            "SMESH IDL stubs are unavailable; meshes cannot be identified",
            exc_info=True,
        )

    try:
        smesh_component = study.FindComponent("SMESH")
    except Exception:
        _logger.warning("SMESH load failed in FindComponent", exc_info=True)
        raise
    if smesh_component is None:
        _logger.debug("SMESH load skipped: study has no SMESH component")
        return None

    try:
        engine = salome.lcc.FindOrLoadComponent("FactoryServer", "SMESH")
    except Exception:
        _logger.warning("SMESH load failed in FindOrLoadComponent", exc_info=True)
        raise
    if engine is None:
        _logger.warning("SMESH load failed: FactoryServer returned no engine")
        return None

    try:
        builder = study.NewBuilder()
        builder.LoadWith(smesh_component, engine)
    except Exception:
        # LoadWith raises when the component is already loaded, which is a
        # success as far as the caller is concerned: it only needs live mesh
        # objects, and an already-loaded component provides them.
        _logger.info(
            "SMESH LoadWith raised; treating the component as already loaded",
            exc_info=True,
        )
    return smesh_component
