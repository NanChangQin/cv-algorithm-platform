from algorithm_manager.infrastructure.docker_runtime import DockerContainerRuntime


def test_runtime_mode_keeps_the_selected_card(tmp_path):
    runtime = DockerContainerRuntime(None, "network", tmp_path, tmp_path, gpu_access_mode="runtime")
    options = runtime._gpu_options(("1",))
    assert options["runtime"] == "nvidia"
    assert options["environment"]["NVIDIA_VISIBLE_DEVICES"] == "1"
    assert options["environment"]["NVIDIA_DRIVER_CAPABILITIES"] == "compute,utility"
    assert "device_requests" not in options
