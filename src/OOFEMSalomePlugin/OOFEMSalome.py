"""Small SALOME lifecycle helpers shared by plugin and module entry points."""


def load_smesh_component(study):
    """Activate the active study's SMESH engine before using mesh CORBA objects.

    SALOME does not necessarily activate SMESH until the Mesh module has been
    opened. AsterStudy performs this same ``FindOrLoadComponent``/``LoadWith``
    sequence from its module activation callback.
    """
    if study is None:
        return None

    import salome

    smesh_component = study.FindComponent("SMESH")
    if smesh_component is None:
        return None
    engine = salome.lcc.FindOrLoadComponent("FactoryServer", "SMESH")
    builder = study.NewBuilder()
    builder.LoadWith(smesh_component, engine)
    return smesh_component
