from nitrostack import ExecutionContext

def create_scope_guard(required_scopes: list):
    class ScopeGuard:
        async def can_activate(self, context: ExecutionContext) -> bool:
            user_scopes = getattr(context.auth, "scopes", [])
            missing_scopes = [s for s in required_scopes if s not in user_scopes]
            if missing_scopes:
                raise ValueError(
                    f"Insufficient scope. Required: {', '.join(required_scopes)}. "
                    f"Missing: {', '.join(missing_scopes)}"
                )
            return True
    return ScopeGuard
