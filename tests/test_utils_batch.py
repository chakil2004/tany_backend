import pytest

import utils


VALID_SLOPE = {
    "max_slope": 12.5,
    "avg_slope": 6.25,
    "individual_slopes": [0.0, 12.5],
}


def mock_batch_services(monkeypatch, *, slope_error=None, climate_error=None):
    def slope(*args, **kwargs):
        if slope_error is not None:
            raise slope_error
        return dict(VALID_SLOPE)

    def climate(*args, **kwargs):
        if climate_error is not None:
            raise climate_error
        return {"rainy_season": {"onset": {"mean_day": 10}}}

    monkeypatch.setattr(utils, "get_elevation", lambda *args, **kwargs: 123.4)
    monkeypatch.setattr(utils, "get_terrain_slope", slope)
    monkeypatch.setattr(
        utils,
        "get_soil_data",
        lambda *args, **kwargs: [
            {"name": "clay", "unit_measure": "%", "values": 12.0}
        ],
    )
    monkeypatch.setattr(
        utils,
        "flood_risk",
        lambda *args, **kwargs: {
            "coordinate": {},
            "flood_risk": {},
            "forecast": {},
            "statistics": {},
        },
    )
    monkeypatch.setattr(utils, "season_analysis", climate)
    monkeypatch.setattr(
        utils,
        "proximity_analysis",
        lambda *args, **kwargs: {
            "query": {},
            "results": {"hospital": [], "market": [], "transport": []},
        },
    )


def get_report():
    return utils.get_all_infos(
        longitude=35.0,
        latitude=31.0,
        uuid_parcel="parcel-test-id",
    ).all_data()


@pytest.mark.parametrize("error_type", [ValueError, TypeError])
def test_all_data_keeps_partial_report_when_slope_calculation_fails(
    monkeypatch, error_type
):
    mock_batch_services(monkeypatch, slope_error=error_type("invalid slope response"))

    report = get_report()

    assert isinstance(report, list)
    assert len(report) == 3
    uuid, value, ers = report
    assert uuid == "parcel-test-id"
    assert value["max_slope"] is None
    assert value["avg_slope"] is None
    assert value["individual_slopes"] == []
    assert "error" in ers["slopes"].lower() or "could not" in ers["slopes"].lower()
    assert ers["slopes"] != "ok!"

    assert value["elevation"] == 123.4
    assert value["soil_value"][0]["name"] == "clay"
    assert value["flood"]["forecast"] == {}
    assert value["climates"]["rainy_season"]["onset"]["mean_day"] == 10
    assert value["proximity"]["results"]["hospital"] == []


def test_all_data_keeps_partial_report_when_nasa_climate_is_insufficient(monkeypatch):
    mock_batch_services(
        monkeypatch,
        climate_error=ValueError("insufficient NASA POWER history"),
    )

    report = get_report()

    assert isinstance(report, list)
    assert len(report) == 3
    uuid, value, ers = report
    assert uuid == "parcel-test-id"
    assert value["climates"] is None
    assert ers["climates"] != "ok!"
    assert "unavailable or insufficient" in ers["climates"]

    assert value["max_slope"] == 12.5
    assert value["avg_slope"] == 6.25
    assert value["individual_slopes"] == [0.0, 12.5]
    assert value["elevation"] == 123.4
    assert value["soil_value"][0]["name"] == "clay"
    assert value["flood"]["forecast"] == {}
    assert value["proximity"]["results"]["hospital"] == []
