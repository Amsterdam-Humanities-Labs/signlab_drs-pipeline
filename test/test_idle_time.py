# Old name, kept so existing callers keep working (signlab_signcollect-stack#51).
# Runs test/manual_idle_time.py only when started as a script, so pytest collects nothing here.
if __name__ == "__main__":
    import os, runpy
    runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "manual_idle_time.py"), run_name="__main__")
