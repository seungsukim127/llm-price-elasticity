.PHONY: install demo test backfill panel analysis

install:
	pip install -r requirements.txt

# Whole pipeline on synthetic data with known answers (no API key needed)
demo:
	rm -rf data_demo
	python -m src.simulate --out data_demo/raw
	python -m src.build_panel --raw data_demo/raw --out data_demo/processed --manual data_demo/manual
	python -m src.run_analysis --processed data_demo/processed --figures data_demo/figures

test:
	python -m pytest -q

# Real data (needs OPENROUTER_API_KEY)
backfill:
	python -m src.collect backfill

panel:
	python -m src.build_panel

analysis: panel
	python -m src.run_analysis
