from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError


class SSMError(Exception):
    pass


class SSMService:
    def __init__(self, client: Any | None = None) -> None:
        if client is not None:
            self._client = client
        else:
            import boto3  # noqa: PLC0415 — lazy import to avoid cold-start overhead
            self._client = boto3.client("ssm")

    def get_parameter(self, name: str, *, with_decryption: bool = True) -> str:
        try:
            response = self._client.get_parameter(Name=name, WithDecryption=with_decryption)
            return response["Parameter"]["Value"]
        except ClientError as exc:
            raise SSMError(f"Failed to get parameter {name!r}: {exc}") from exc

    def put_parameter(self, name: str, value: str, *, overwrite: bool = False) -> None:
        try:
            self._client.put_parameter(
                Name=name,
                Value=value,
                Type="SecureString",
                Overwrite=overwrite,
            )
        except ClientError as exc:
            raise SSMError(f"Failed to put parameter {name!r}: {exc}") from exc

    def bootstrap_agent_ids(
        self,
        agent_map: dict[str, str],
        *,
        overwrite: bool = False,
    ) -> dict[str, str]:
        """Bootstrap SSM parameters idempotently.

        For each (name, value) in ``agent_map``:
          - ``overwrite=True``: always write the parameter.
          - ``overwrite=False``: skip if it already exists, otherwise create it.

        All parameter names must start with ``/specforge/``; other names raise
        ``SSMError`` before any writes occur.

        Returns a mapping ``{name: "created" | "skipped" | "overwritten"}``
        describing what happened for each input.
        """
        invalid = [name for name in agent_map if not name.startswith("/specforge/")]
        if invalid:
            raise SSMError(
                f"Invalid parameter name(s) (must start with '/specforge/'): {invalid!r}"
            )

        status: dict[str, str] = {}
        for name, value in agent_map.items():
            if overwrite:
                self.put_parameter(name, value, overwrite=True)
                status[name] = "overwritten"
                continue

            exists = self._parameter_exists(name)
            if exists:
                status[name] = "skipped"
            else:
                self.put_parameter(name, value, overwrite=False)
                status[name] = "created"
        return status

    def _parameter_exists(self, name: str) -> bool:
        try:
            self._client.get_parameter(Name=name, WithDecryption=False)
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "ParameterNotFound":
                return False
            raise SSMError(
                f"Failed to check existence of parameter {name!r}: {exc}"
            ) from exc
