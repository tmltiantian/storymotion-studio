from factory.pipeline_executors import registered_executor_ids


def test_every_mode_step_has_a_registered_native_executor() -> None:
    assert set(registered_executor_ids()) == {
        "generic.concept",
        "original.script",
        "novel.script",
        "generic.storyboard",
        "generic.assets",
        "generic.audio",
        "generic.video",
        "generic.edit",
        "generic.eval",
        "generic.deliver",
        "replica.concept",
        "replica.script",
        "replica.storyboard",
        "replica.assets",
        "replica.audio",
        "replica.video",
        "replica.edit",
        "replica.eval",
        "replica.deliver",
    }
