export interface McpToolInfo { name: string; description: string; writes_prediction: boolean }
export interface McpConfig { resource_url: string; max_calls_per_minute: number; tools: McpToolInfo[]; stdio_command: string; stdio_args: string[] }
export interface McpCredential { credential_id: string; client_name: string; scopes: string[]; created_at: string; expires_at: string; revoked_at: string | null; status: string }
export interface McpGrant { client_name: string; scopes: string[]; expires_minutes: number; idempotency_key: string }
export interface McpActivity { request_id: string; created_at: string; client_name: string; operation: string; method: string; transport: string; status: string; duration_ms: number; error_code?: string }
