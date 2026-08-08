"""Schema-driven VISA driver with explicit mutation safety boundaries."""

from __future__ import annotations

import contextvars
import logging
import math
import numbers
import re
import string
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pyvisa
import yaml

logger = logging.getLogger(__name__)

_COMMAND_SEPARATORS = (";", "\n", "\r")
_READ_OPERATIONS = frozenset(("read", "query"))
_WRITE_OPERATIONS = frozenset(("write", "mutate", "mutating"))


class UniversalDriverError(Exception):
    """Base class for UniversalDriver failures."""


class SchemaSafetyError(UniversalDriverError):
    """Raised when a schema or rendered command violates a safety boundary."""


class MutationSafetyError(UniversalDriverError):
    """Raised when a mutating command is not safe to execute."""


class InstrumentCommandError(UniversalDriverError):
    """A transport failure retaining both the original and instrument errors."""

    def __init__(
        self,
        *,
        original_error: Exception,
        command: str,
        operation: str,
        instrument_error: str | None = None,
    ) -> None:
        self.original_error = original_error
        self.command = command
        self.operation = operation
        self.instrument_error = instrument_error
        # A descriptive alias for callers that model this as the SCPI error queue.
        self.instrument_error_queue = instrument_error
        message = f"instrument {operation} failed for {command!r}: {original_error}"
        if instrument_error is not None:
            message += f" (instrument error queue: {instrument_error})"
        super().__init__(message)

    def as_dict(self) -> dict[str, Any]:
        """Return a logging/serialization-friendly representation."""
        return {
            "command": self.command,
            "operation": self.operation,
            "original_error": self.original_error,
            "instrument_error": self.instrument_error,
        }


class UniversalDriver:
    """Dynamically expose commands from a YAML capability schema.

    Schemas are untrusted by default. Legacy string commands may only perform
    queries whose query marker is present in the template itself. A mutation
    needs explicit write metadata, out-of-band schema trust, a matching *IDN?
    response, complete numeric bounds, and an active :meth:`armed` context.
    """

    def __init__(
        self,
        resource_name: str,
        schema_path: str | Path,
        *,
        resource_manager: Any | None = None,
        instrument: Any | None = None,
        trusted_schema: bool = False,
        schema_trusted: bool | None = None,
        expected_identity: Mapping[str, str] | str | None = None,
        expected_manufacturer: str | None = None,
        expected_model: str | None = None,
        expected_serial: str | None = None,
    ) -> None:
        if schema_trusted is not None:
            if trusted_schema and not schema_trusted:
                raise ValueError("conflicting schema trust arguments")
            trusted_schema = schema_trusted

        self.resource_name = resource_name
        self._owns_resource_manager = resource_manager is None and instrument is None
        self.rm = resource_manager
        opened_instrument = False
        try:
            if instrument is None:
                if self.rm is None:
                    self.rm = pyvisa.ResourceManager()
                instrument = self.rm.open_resource(resource_name)
                opened_instrument = True
            self.instrument: Any = instrument
            # Pin the transport object used to establish identity. A matching
            # response from a subsequently substituted object must not inherit
            # this driver's mutation authority.
            self._identity_instrument: Any = instrument
            self.schema_trusted = bool(trusted_schema)
            self.identity_verified = False
            self.identity_response: str | None = None
            self.identity_fields: tuple[str, str, str] | None = None
            self.expected_identity = self._expected_identity_fields(
                expected_identity,
                manufacturer=expected_manufacturer,
                model=expected_model,
                serial=expected_serial,
            )
            self._armed_context: contextvars.ContextVar[bool] = contextvars.ContextVar(
                f"universal_driver_armed_{id(self)}", default=False
            )

            with Path(schema_path).open("r", encoding="utf-8") as schema_file:
                loaded = yaml.safe_load(schema_file)
            if not isinstance(loaded, Mapping):
                raise SchemaSafetyError("schema root must be a mapping")
            self.schema = dict(loaded)

            self._setup_capabilities()
            # Verification is read-only and attempted for trusted schemas. Failure
            # leaves the driver usable for reads but unable to mutate.
            if self.schema_trusted:
                self.verify_identity()
        except BaseException:
            # Preserve the initialization error. Cleanup failures must neither mask it
            # nor prevent an independently owned manager from being released.
            if opened_instrument and instrument is not None:
                try:
                    instrument.close()
                except Exception:
                    logger.warning("instrument cleanup after initialization failure failed", exc_info=True)
            if self._owns_resource_manager and self.rm is not None:
                try:
                    self.rm.close()
                except Exception:
                    logger.warning("resource-manager cleanup after initialization failure failed", exc_info=True)
            raise

    def _setup_capabilities(self) -> None:
        """Create methods only after every schema command name passes collision checks."""
        capabilities = self.schema.get("capabilities", {})
        if not isinstance(capabilities, Mapping):
            raise SchemaSafetyError("schema capabilities must be a mapping")

        commands_to_install: list[tuple[str, Any]] = []
        seen_names: set[str] = set()
        # Dynamic schema methods share the driver instance namespace. Snapshot every
        # inherited/class and initialized instance attribute before installing any
        # command so a schema can never shadow a trust, identity, arming, transport,
        # or lifecycle control.
        reserved_names = set(dir(self)) | set(vars(self))
        for cap_data in capabilities.values():
            if not isinstance(cap_data, Mapping):
                raise SchemaSafetyError("each capability must be a mapping")
            commands = cap_data.get("commands", {})
            if not isinstance(commands, Mapping):
                raise SchemaSafetyError("capability commands must be a mapping")
            for cmd_name, command_spec in commands.items():
                if not isinstance(cmd_name, str) or not cmd_name.isidentifier():
                    raise SchemaSafetyError(f"invalid command method name: {cmd_name!r}")
                if cmd_name.startswith("_") or cmd_name in reserved_names:
                    raise SchemaSafetyError(
                        f"dynamic command name {cmd_name!r} collides with a reserved driver attribute"
                    )
                if cmd_name in seen_names:
                    raise SchemaSafetyError(f"duplicate dynamic command name: {cmd_name!r}")
                seen_names.add(cmd_name)
                commands_to_install.append((cmd_name, command_spec))

        for cmd_name, command_spec in commands_to_install:
            setattr(self, cmd_name, self._create_cmd_method(cmd_name, command_spec))

    def _create_cmd_method(self, command_name: str, command_spec: Any):
        legacy = isinstance(command_spec, str)
        if legacy:
            template = command_spec
            # Inspect the schema template, never caller-controlled rendered text.
            operation = "read" if self._template_contains_query(template) else "legacy-write"
            parameters: Mapping[str, Any] = {}
        elif isinstance(command_spec, Mapping):
            template = command_spec.get("template", command_spec.get("command"))
            operation_value = command_spec.get("operation")
            if not isinstance(template, str) or not template:
                raise SchemaSafetyError(f"command {command_name!r} requires a string template")
            if not isinstance(operation_value, str):
                raise SchemaSafetyError(
                    f"command {command_name!r} requires explicit read/write operation metadata"
                )
            operation = operation_value.strip().lower()
            if operation not in _READ_OPERATIONS | _WRITE_OPERATIONS:
                raise SchemaSafetyError(
                    f"command {command_name!r} has unsupported operation {operation_value!r}"
                )
            if operation in _READ_OPERATIONS and not template.rstrip().endswith("?"):
                raise SchemaSafetyError(
                    f"read command {command_name!r} requires a terminal query marker '?'"
                )
            parameters_value = command_spec.get("parameters", command_spec.get("bounds", {}))
            if not isinstance(parameters_value, Mapping):
                raise SchemaSafetyError(f"command {command_name!r} parameters must be a mapping")
            parameters = parameters_value
        else:
            raise SchemaSafetyError(
                f"command {command_name!r} must be a legacy string or metadata mapping"
            )

        self._reject_multiple_commands(template)

        def method(*args: Any, **kwargs: Any) -> Any:
            if operation == "legacy-write":
                raise SchemaSafetyError(
                    f"legacy command {command_name!r} is read-only; add explicit operation "
                    "and parameter metadata before enabling mutation"
                )
            is_write = operation in _WRITE_OPERATIONS
            dispatch_instrument = self.instrument
            if is_write:
                # Validate trust, identity, arming, and raw argument values before
                # formatting any caller-controlled data into instrument syntax.
                args, kwargs = self._assert_mutation_allowed(
                    template, args, kwargs, parameters, dispatch_instrument
                )
            try:
                command = template.format(*args, **kwargs)
            except (IndexError, KeyError, ValueError, AttributeError) as error:
                raise SchemaSafetyError(
                    f"could not safely render command {command_name!r}: {error}"
                ) from error
            self._reject_multiple_commands(command)

            dispatch_operation = "write" if is_write else "read"
            if is_write and self.instrument is not dispatch_instrument:
                self.identity_verified = False
                raise MutationSafetyError("instrument object changed during mutation authorization")
            try:
                if is_write:
                    return dispatch_instrument.write(command)
                return dispatch_instrument.query(command)
            except Exception as error:
                raise self._instrument_error(error, command, dispatch_operation) from error

        method.__name__ = command_name
        method.__doc__ = f"Execute schema command {command_name!r} ({operation})."
        return method

    @staticmethod
    def _template_contains_query(template: str) -> bool:
        """Detect a query only in static template text, not replacement fields."""
        try:
            return any("?" in literal for literal, _, _, _ in string.Formatter().parse(template))
        except ValueError as error:
            raise SchemaSafetyError(f"invalid command template: {error}") from error

    @staticmethod
    def _reject_multiple_commands(command: str) -> None:
        if any(separator in command for separator in _COMMAND_SEPARATORS):
            raise SchemaSafetyError("command separator or multiple commands are not allowed")

    def verify_identity(self) -> bool:
        """Verify schema compatibility and an exact, out-of-band physical binding."""
        return self._verify_identity_on(self.instrument)

    def _verify_identity_on(self, instrument: Any) -> bool:
        """Verify identity using one transport object for the complete query."""
        identification = self.schema.get("identification", {})
        pattern = identification.get("idn_pattern") if isinstance(identification, Mapping) else None
        self.identity_verified = False
        self.identity_fields = None
        if not isinstance(pattern, str) or not pattern:
            return False
        try:
            response = str(instrument.query("*IDN?")).strip().replace("\x00", "")
            self.identity_response = response
            fields = self._parse_idn(response)
            self.identity_fields = fields
            schema_matches = re.search(pattern, response, re.IGNORECASE) is not None
            self.identity_verified = (
                schema_matches
                and fields is not None
                and self.expected_identity is not None
                and self._identities_equal(fields, self.expected_identity)
            )
        except (Exception, re.error):
            logger.warning("instrument identity verification failed", exc_info=True)
        return self.identity_verified

    @staticmethod
    def _parse_idn(response: str) -> tuple[str, str, str] | None:
        parts = tuple(part.strip() for part in response.split(","))
        if len(parts) < 3 or any(not part for part in parts[:3]):
            return None
        return parts[0], parts[1], parts[2]

    @classmethod
    def _expected_identity_fields(
        cls,
        expected_identity: Mapping[str, str] | str | None,
        *,
        manufacturer: str | None,
        model: str | None,
        serial: str | None,
    ) -> tuple[str, str, str] | None:
        if isinstance(expected_identity, str):
            from_identity = cls._parse_idn(expected_identity)
            if from_identity is None:
                raise ValueError("expected_identity must contain manufacturer, model, and serial")
        elif isinstance(expected_identity, Mapping):
            from_identity = (
                str(expected_identity.get("manufacturer", "")).strip(),
                str(expected_identity.get("model", "")).strip(),
                str(expected_identity.get("serial", "")).strip(),
            )
        elif expected_identity is None:
            from_identity = None
        else:
            raise TypeError("expected_identity must be a mapping, IDN string, or None")

        explicit = (
            (manufacturer or "").strip(),
            (model or "").strip(),
            (serial or "").strip(),
        )
        if any(explicit):
            if not all(explicit):
                raise ValueError(
                    "expected_manufacturer, expected_model, and expected_serial must all be set"
                )
            if from_identity is not None and not cls._identities_equal(from_identity, explicit):
                raise ValueError("conflicting expected identity bindings")
            from_identity = explicit
        if from_identity is None:
            return None
        if not all(from_identity):
            raise ValueError("expected identity binding requires manufacturer, model, and serial")
        return from_identity

    @staticmethod
    def _identities_equal(
        actual: tuple[str, str, str], expected: tuple[str, str, str]
    ) -> bool:
        return tuple(value.casefold() for value in actual) == tuple(
            value.casefold() for value in expected
        )

    @contextmanager
    def armed(self) -> Iterator["UniversalDriver"]:
        """Temporarily arm mutations in this task/thread execution context."""
        token = self._armed_context.set(True)
        try:
            yield self
        finally:
            self._armed_context.reset(token)

    # A short alias that reads naturally as ``with driver.arm():``.
    arm = armed

    def _assert_mutation_allowed(
        self,
        template: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        parameters: Mapping[str, Any],
        instrument: Any,
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        if not self.schema_trusted:
            raise MutationSafetyError("mutating commands require an explicitly trusted schema")
        if self.expected_identity is None:
            raise MutationSafetyError(
                "mutating commands require an expected out-of-band manufacturer/model/serial binding"
            )
        if not self._armed_context.get():
            raise MutationSafetyError("mutating commands require an active armed execution context")
        if instrument is not self._identity_instrument:
            self.identity_verified = False
            raise MutationSafetyError(
                "instrument object changed after identity binding; mutation identity is no longer verified"
            )
        # Cached identity is insufficient at a mutation boundary: re-read the
        # physical instrument immediately before every armed write so a device
        # replacement or changed identity fails closed.
        if not self._verify_identity_on(instrument):
            raise MutationSafetyError("mutating commands require exact verified instrument identity")
        return self._validate_numeric_bounds(template, args, kwargs, parameters)

    @staticmethod
    def _validate_numeric_bounds(
        template: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        parameters: Mapping[str, Any],
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        formatter = string.Formatter()
        checked: set[str] = set()
        normalized_args = list(args)
        normalized_kwargs = dict(kwargs)
        try:
            parsed = formatter.parse(template)
            for _, field_name, _, _ in parsed:
                if field_name is None or field_name in checked:
                    continue
                checked.add(field_name)
                value, _ = formatter.get_field(field_name, args, kwargs)
                numeric_value = UniversalDriver._numeric_value(value)
                constraint = parameters.get(field_name)
                if constraint is None:
                    raise MutationSafetyError(
                        f"parameter {field_name!r} requires an explicit bounds or enum constraint"
                    )
                enum = constraint.get("enum") if isinstance(constraint, Mapping) else None
                if enum is not None:
                    if (
                        not isinstance(enum, Sequence)
                        or isinstance(enum, (str, bytes, bytearray))
                        or not any(
                            type(value) is type(member) and value == member for member in enum
                        )
                    ):
                        raise MutationSafetyError(
                            f"parameter {field_name!r}={value!r} is not in its declared enum"
                        )
                endpoints = UniversalDriver._bound_endpoints(constraint)
                if endpoints is None:
                    if numeric_value is None:
                        if enum is not None:
                            continue
                        raise MutationSafetyError(
                            f"non-numeric parameter {field_name!r} requires a declared enum"
                        )
                    raise MutationSafetyError(
                        f"numeric parameter {field_name!r} requires explicit bounds"
                    )
                lower, upper = endpoints
                if numeric_value is None:
                    raise MutationSafetyError(
                        f"bounded parameter {field_name!r} requires a numeric value"
                    )
                if (
                    isinstance(lower, bool)
                    or isinstance(upper, bool)
                    or not isinstance(lower, numbers.Real)
                    or not isinstance(upper, numbers.Real)
                    or not math.isfinite(float(lower))
                    or not math.isfinite(float(upper))
                    or lower > upper
                ):
                    raise MutationSafetyError(
                        f"numeric parameter {field_name!r} requires finite minimum/maximum bounds"
                    )
                if (
                    not math.isfinite(float(numeric_value))
                    or numeric_value < lower
                    or numeric_value > upper
                ):
                    raise MutationSafetyError(
                        f"numeric parameter {field_name!r}={value!r} is outside bounds "
                        f"[{lower!r}, {upper!r}]"
                    )
                if isinstance(value, str):
                    if field_name in normalized_kwargs:
                        normalized_kwargs[field_name] = numeric_value
                    elif field_name.isdecimal():
                        normalized_args[int(field_name)] = numeric_value
        except (KeyError, IndexError, AttributeError) as error:
            raise SchemaSafetyError(f"could not validate command parameters: {error}") from error
        return tuple(normalized_args), normalized_kwargs

    @staticmethod
    def _numeric_value(value: Any) -> Any:
        """Return the numeric meaning of real values and decimal lexical strings."""
        if isinstance(value, bool):
            return None
        if isinstance(value, numbers.Real):
            return value
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return None
        return None

    @staticmethod
    def _bound_endpoints(bounds: Any) -> tuple[Any, Any] | None:
        """Normalize verbose and compact finite-range metadata forms."""
        if isinstance(bounds, Mapping):
            nested = bounds.get("bounds")
            if nested is not None:
                return UniversalDriver._bound_endpoints(nested)
            if "minimum" in bounds or "min" in bounds or "maximum" in bounds or "max" in bounds:
                return (
                    bounds.get("minimum", bounds.get("min")),
                    bounds.get("maximum", bounds.get("max")),
                )
            return None
        if (
            isinstance(bounds, Sequence)
            and not isinstance(bounds, (str, bytes, bytearray))
            and len(bounds) == 2
        ):
            return bounds[0], bounds[1]
        return None

    def _instrument_error(
        self, original_error: Exception, command: str, operation: str
    ) -> InstrumentCommandError:
        instrument_error: str | None = None
        try:
            instrument_error = str(self.instrument.query(":SYSTem:ERRor?")).strip()
        except Exception:
            # The optional queue information must never replace the root cause.
            pass
        logger.error(
            "instrument command failed",
            extra={
                "command": command,
                "operation": operation,
                "instrument_error": instrument_error,
            },
            exc_info=original_error,
        )
        return InstrumentCommandError(
            original_error=original_error,
            command=command,
            operation=operation,
            instrument_error=instrument_error,
        )

    def close(self) -> None:
        try:
            self.instrument.close()
        finally:
            if self._owns_resource_manager and self.rm is not None:
                self.rm.close()
