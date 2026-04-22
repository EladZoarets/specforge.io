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
          - ``overwrite=True``: always write the parameter. Status reflects
            whether the parameter existed before the write (``"overwritten"``)
            or was freshly created (``"created"``).
          - ``overwrite=False``: skip if it already exists, otherwise create it.
            Handles the check-then-put race: if another writer creates the
            parameter between the existence check and our put, the status is
            still ``"skipped"`` (not an error).

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
                existed = self._parameter_exists(name)
                self.put_parameter(name, value, overwrite=True)
                status[name] = "overwritten" if existed else "created"
                continue

            # Fast-path check to avoid a stray write on 99% of calls, but still
            # handle the race if existence changes between check and put.
            if self._parameter_exists(name):
                status[name] = "skipped"
                continue
            created = self._try_create(name, value)
            status[name] = "created" if created else "skipped"
        return status

    def _try_create(self, name: str, value: str) -> bool:
        """Create a new SecureString parameter.

        Returns ``True`` if created, ``False`` if it already existed. Other
        ``ClientError``s re-raise as :class:`SSMError`.
        """
        try:
            self._client.put_parameter(
                Name=name,
                Value=value,
                Type="SecureString",
                Overwrite=False,
            )
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "ParameterAlreadyExists":
                return False
            raise SSMError(f"Failed to create parameter {name!r}: {exc}") from exc

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
