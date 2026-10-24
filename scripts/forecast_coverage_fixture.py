"""Recorded, explicitly synthetic UKMO/ECMWF/ICON coverage regression data."""
from pathlib import Path
from alerts import ForecastBundle, save_bundle
from config import forecast_locations
from tests.test_forecast_coverage import START, missing_model_fixture

if __name__ == '__main__':
    path=Path('var/coverage-snapshot.json')
    path.parent.mkdir(exist_ok=True)
    save_bundle(ForecastBundle(tuple(f for p in forecast_locations() for f in missing_model_fixture(p)),
                              (),.5,('Synthetic provider gaps for model coverage validation.',),START),path)
