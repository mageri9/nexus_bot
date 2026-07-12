import pytest
from core.signal import Signal
from services.classifier import Classifier


@pytest.mark.asyncio
async def test_classify_resource_stopped(fake_redis):
    classifier = Classifier(fake_redis)
    signal = Signal(
        project="nexus",
        resource="app",
        source="collector",
        event_type="ResourceStopped",
        status="exited"
    )

    cs = await classifier.classify(signal)
    assert cs.severity == "HIGH"
    assert cs.action == "process"


@pytest.mark.asyncio
async def test_classify_resource_stopped_under_maintenance(fake_redis):
    classifier = Classifier(fake_redis)
    # Устанавливаем режим обслуживания в Redis
    await fake_redis.set("nexus:maintenance:nexus", "1")

    signal = Signal(
        project="nexus",
        resource="app",
        source="collector",
        event_type="ResourceStopped",
        status="exited"
    )

    cs = await classifier.classify(signal)
    assert cs.action == "ignore"
    assert cs.severity == "INFO"


@pytest.mark.asyncio
async def test_classify_devops_workflow_success(fake_redis):
    classifier = Classifier(fake_redis)
    signal = Signal(
        project="nexus",
        resource="deploy",
        source="devops",
        event_type="devops:workflow_success",
        status="success"
    )

    cs = await classifier.classify(signal)
    assert cs.severity == "SUCCESS"
    assert cs.action == "process"


@pytest.mark.asyncio
async def test_classify_sdk_app_error(fake_redis):
    classifier = Classifier(fake_redis)
    signal = Signal(
        project="tarot_bot",
        resource="app",
        source="sdk",
        event_type="app:error",
        status="error"
    )

    cs = await classifier.classify(signal)
    assert cs.severity == "HIGH"
    assert cs.action == "process"