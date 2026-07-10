import pytest
from services.health_engine import HealthEngine


@pytest.fixture
def engine():
    return HealthEngine()


def container(status="running", cpu="10.00%", mem_perc="10.00%", restarts=0):
    return {
        "status": status,
        "metrics": {
            "cpu": {"value": cpu},
            "mem_perc": {"value": mem_perc},
            "restarts": {"value": restarts},
        },
    }


def test_empty_or_error_state_scores_zero(engine):
    assert engine.calculate_score({}) == 0
    assert engine.calculate_score({"error": "no data"}) == 0


def test_missing_version_2_returns_sentinel_minus_one(engine):
    # Легаси V1-стейт (без "version": 2) — движок сигнализирует "нет данных" через -1
    assert engine.calculate_score({"containers": {}}) == -1
    assert engine.calculate_score({"version": 1}) == -1


def test_all_healthy_scores_100(engine):
    state = {
        "version": 2,
        "containers": {
            "app": container(status="running"),
            "worker": container(status="healthy"),
        },
        "storage": {
            "data_disk": {"status": "healthy"},
        },
    }
    assert engine.calculate_score(state) == 100


def test_stopped_container_penalizes_50(engine):
    state = {
        "version": 2,
        "containers": {"app": container(status="exited")},
        "storage": {},
    }
    assert engine.calculate_score(state) == 50


def test_stopped_redis_or_postgres_gets_extra_penalty(engine):
    state = {
        "version": 2,
        "containers": {"redis": container(status="exited")},
        "storage": {},
    }
    # -50 (сбой) - 20 (критическая СУБД) = 30
    assert engine.calculate_score(state) == 30

    state_pg = {
        "version": 2,
        "containers": {"postgres": container(status="exited")},
        "storage": {},
    }
    assert engine.calculate_score(state_pg) == 30


def test_high_cpu_penalizes_15(engine):
    state = {
        "version": 2,
        "containers": {"app": container(cpu="99.50%")},
        "storage": {},
    }
    assert engine.calculate_score(state) == 85


def test_high_mem_penalizes_15(engine):
    state = {
        "version": 2,
        "containers": {"app": container(mem_perc="95.00%")},
        "storage": {},
    }
    assert engine.calculate_score(state) == 85


def test_cpu_and_mem_thresholds_are_exclusive_not_inclusive(engine):
    # Ровно 95.0% / 90.0% не должно штрафоваться — правило использует строгое ">"
    state = {
        "version": 2,
        "containers": {"app": container(cpu="95.00%", mem_perc="90.00%")},
        "storage": {},
    }
    assert engine.calculate_score(state) == 100


def test_restarts_penalize_5_points_each(engine):
    state = {
        "version": 2,
        "containers": {"app": container(restarts=3)},
        "storage": {},
    }
    assert engine.calculate_score(state) == 85


def test_unhealthy_storage_penalizes_50(engine):
    state = {
        "version": 2,
        "containers": {},
        "storage": {"data_disk": {"status": "unhealthy"}},
    }
    assert engine.calculate_score(state) == 50


def test_score_never_goes_below_zero(engine):
    state = {
        "version": 2,
        "containers": {
            "redis": container(status="exited", cpu="99%", mem_perc="99%", restarts=50),
            "postgres": container(status="exited"),
        },
        "storage": {"data_disk": {"status": "unhealthy"}},
    }
    assert engine.calculate_score(state) == 0


def test_malformed_cpu_value_is_ignored_not_raised(engine):
    state = {
        "version": 2,
        "containers": {"app": container(cpu="n/a")},
        "storage": {},
    }
    # Не должно кидать исключение, просто не штрафуется
    assert engine.calculate_score(state) == 100


def test_metric_raw_value_without_dict_wrapper_is_supported(engine):
    # health_engine поддерживает как обёрнутый Metric.value, так и сырое значение
    state = {
        "version": 2,
        "containers": {
            "app": {
                "status": "running",
                "metrics": {
                    "cpu": "99.99%",
                    "mem_perc": "99.99%",
                    "restarts": 2,
                },
            }
        },
        "storage": {},
    }
    # -15 (cpu) -15 (mem) -10 (2 restarts) = 60
    assert engine.calculate_score(state) == 60
