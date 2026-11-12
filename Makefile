
.PHONY: install demo test backfill panel analysis

# Install Python libraries (pandas, numpy, statsmodels, matplotlib, etc.)
install:
	pip install -r requirements.txt

# Whole pipeline on synthetic data with known answers (no API key needed)
demo:
	rm -rf data_demo
	python -m src.simulate --out data_demo/raw
	python -m src.build_panel --raw data_demo/raw --out data_demo/processed --manual data_demo/manual
	python -m src.run_analysis --processed data_demo/processed --figures data_demo/figures

# Automatic pass/fail check
test:
	python -m pytest -q

# Real data (needs OPENROUTER_API_KEY)
backfill:
	python -m src.collect backfill

# Turn raw API snapshots in data/raw into analysis tables in data/processed.
panel:
	python -m src.build_panel

analysis: panel
	python -m src.run_analysis
