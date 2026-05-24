"""Tests for gateway security: config key validation, value validation."""

from gateway.security import validate_config_keys, validate_config_values


class TestValidateConfigKeys:
    def test_allowed_keys_pass(self):
        data = {"OPENAI_KEY": "sk-test", "LOCAL_MODEL": "llama3"}
        assert validate_config_keys(data) == []

    def test_rejected_keys_caught(self):
        data = {"PATH": "/evil", "LD_PRELOAD": "bad.so"}
        rejected = validate_config_keys(data)
        assert "PATH" in rejected
        assert "LD_PRELOAD" in rejected

    def test_mixed_keys(self):
        data = {"OPENAI_KEY": "sk-test", "EVIL_KEY": "x", "HOME": "/root"}
        rejected = validate_config_keys(data)
        assert "OPENAI_KEY" not in rejected
        assert "EVIL_KEY" in rejected
        assert "HOME" in rejected

    def test_empty_data(self):
        assert validate_config_keys({}) == []

    def test_role_assignment_keys_allowed(self):
        data = {
            "ROUTER_MODEL_MODE": "openai",
            "EXECUTOR_MODEL_MODE": "local",
        }
        assert validate_config_keys(data) == []


class TestValidateConfigValues:
    def test_normal_values_pass(self):
        data = {"OPENAI_KEY": "sk-abc123", "LOCAL_URL": "http://localhost:11434"}
        assert validate_config_values(data) == []

    def test_oversized_value(self):
        data = {"OPENAI_KEY": "x" * 501}
        assert validate_config_values(data) == ["OPENAI_KEY"]

    def test_newline_injection(self):
        data = {"CLOUD_KEY": "valid-key\nMALICIOUS=injected"}
        assert validate_config_values(data) == ["CLOUD_KEY"]

    def test_null_byte_injection(self):
        data = {"CLOUD_KEY": "key\x00evil"}
        assert validate_config_values(data) == ["CLOUD_KEY"]

    def test_carriage_return_injection(self):
        data = {"CLOUD_KEY": "key\revil"}
        assert validate_config_values(data) == ["CLOUD_KEY"]

    def test_non_string_values_ignored(self):
        data = {"PORT": 8080}
        assert validate_config_values(data) == []

    def test_max_boundary_value(self):
        data = {"KEY": "x" * 500}
        assert validate_config_values(data) == []
        data["KEY"] = "x" * 501
        assert validate_config_values(data) == ["KEY"]
