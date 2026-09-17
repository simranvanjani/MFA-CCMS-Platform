"""Task 11: idempotent end-to-end rebuild of the data + serving pipeline.

Runs schema -> data gen -> parse -> L1 extract -> L2 enrich -> governance -> indexes.
The Genie space (genie/create_space.py), dashboard (07_dashboard.py) and app are one-time
setup steps run separately.

Run: python -m pipeline.run_all
"""
import importlib


def step(mod_name, fn="main", *args):
    mod = importlib.import_module(mod_name)
    print(f"\n=== {mod_name}.{fn} ===")
    getattr(mod, fn)(*args)


def main():
    step("pipeline.00_schema", "main")
    gen = importlib.import_module("pipeline.01_generate_data")
    print("\n=== 01_generate_data ===")
    gen.generate(); gen.upload()
    step("pipeline.02_parse", "full")
    step("pipeline.03_extract_l1", "full")
    step("pipeline.04_enrich_l2", "extract")
    step("pipeline.04_enrich_l2", "build_gold")
    step("pipeline.05_governance", "main")
    step("pipeline.06_indexes", "main")
    print("\nDone. One-time setup (run separately): genie.create_space, pipeline.07_dashboard, app deploy.")


if __name__ == "__main__":
    main()
