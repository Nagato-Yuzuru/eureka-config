# Worked shapes

Canonical forms for style-python rules — read when writing the corresponding kind of code. Each block is the skeleton to reproduce, not a tutorial.

## Capability injection & composition root

```python
class CodeStore(Protocol):  # defined by the consumer, sized to what it uses
    async def take(self, key: str) -> str | None: ...


class CodeVerifier:  # logic: receives the code value's source, never builds it
    def __init__(self, store: CodeStore, clock: Callable[[], datetime]) -> None: ...


# composition root — the only place that knows concretions and owns lifecycles
async def main() -> None:
    settings = Settings()  # pydantic-settings: the one environment read
    async with AsyncExitStack() as stack:
        redis = await stack.enter_async_context(build_redis(settings))  # shutdown registered
        verifier = CodeVerifier(store=RedisCodeStore(redis), clock=partial(datetime.now, UTC))
        ...
```

With dependency-injector, the declarative container class is the composition root and `Resource` providers carry the shutdown hooks.

## TaskGroup scope

```python
async with asyncio.TaskGroup() as tg:  # first failure cancels siblings
    for item in items:
        tg.create_task(process(item))
# failures surface here as ExceptionGroup:
#   try: ...
#   except* httpx.HTTPError as eg: ...
# timeouts are scopes too:
#   async with asyncio.timeout(5): ...
```

## Shared Annotated vocabulary

```python
# fields.py — the one home for recurring field logic; the whole codebase imports it
UtcTime = Annotated[datetime, BeforeValidator(parse_utc)]
NonEmptyStr = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


class Order(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    placed_at: UtcTime
    note: NonEmptyStr
```

## Branded values

```python
UserId = NewType("UserId", str)  # no validation needed: zero-cost brand


class Email(RootModel[Annotated[str, StringConstraints(pattern=r".+@.+")]]):
    model_config = ConfigDict(frozen=True)  # construction must validate: RootModel
```

## Generator pipeline

```python
def parse(lines: Iterable[str]) -> Iterator[Record]: ...
def enrich(records: Iterable[Record]) -> Iterator[Record]: ...


emit(enrich(parse(source)))  # lazy, constant memory, early termination
```

## Exhaustive match

```python
match event:
    case Created():
        ...
    case Deleted():
        ...
    case _ as unreachable:
        assert_never(unreachable)  # ty fails here when a variant is unhandled
```
