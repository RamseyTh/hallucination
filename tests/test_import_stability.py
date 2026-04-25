def test_import_chain():
    """This test ensures that all major modules can be imported without error.
    
    A failure here indicates a circular dependency or other module-level import issue.
    """
    try:
        import airs_hv.cli
        import airs_hv.experiment
        import airs_hv.pipeline
        import airs_hv.schema
        import airs_hv.trace
        import airs_hv.generator
        import airs_hv.stages
    except ImportError as e:
        assert False, f"Failed to import a core module: {e}"
