"""Shared fixtures: synthetic survey-field jobs built from the real models."""
import pytest

from adjustflow import stochastic


def make_point(name, easting, northing, elevation, rms=0.010,
               rms_u=0.015, solution="FIX", session_pts=None):
    from field.models import SurveyPoint
    return SurveyPoint(
        name=name, easting=easting, northing=northing, elevation=elevation,
        rms_e=rms, rms_n=rms, rms_u=rms_u, rms_lateral=rms * 1.4,
        solution=solution, antenna_height_m=2.0, samples=25, pdop=1.8,
        baseline_m=500.0)


def make_project():
    """Two sessions, three occupations of point 101, one of 102, one FLOAT."""
    from field.models import BaseStation, Project, RoverSession
    base = BaseStation(id="BASE-1", easting=500000.0, northing=4500000.0,
                       elevation=100.0, n_points=5)
    s1 = RoverSession(id="2026-09-25_BASE-1", base_station_id="BASE-1",
                      date="2026-09-25", points=[
                          make_point("101", 500100.00, 4500100.00, 105.00),
                          make_point("101", 500100.02, 4500099.99, 105.02),
                          make_point("102", 500200.00, 4500200.00, 110.00),
                      ])
    s2 = RoverSession(id="2026-09-26_BASE-1", base_station_id="BASE-1",
                      date="2026-09-26", points=[
                          make_point("101", 500099.99, 4500100.01, 104.99),
                          make_point("103", 500300.00, 4500300.00, 115.00,
                                     solution="FLOAT"),
                      ])
    return Project(name="test-job", crs="NAD83(2011) / UTM zone 18N",
                   source_files=["day1.csv", "day2.csv"],
                   base_stations=[base], sessions=[s1, s2])


@pytest.fixture
def project():
    return make_project()


@pytest.fixture
def cfg():
    return stochastic.default_weights()
