# Configuration persistence verification

Persistent settings are split by security boundary:

| Category | Storage |
|---|---|
| 15 global `AppSettings` fields, including the auto-start preference and display timezone | SQLite table `app_config` |
| Task name, URL, source type, interval, enabled state, keywords, LLM toggle, and adapter selectors | SQLite table `monitor_task` |
| LLM API key | Windows Credential Manager |
| SMTP password / authorization code | Windows Credential Manager |

The formal verification command is:

```powershell
D:\anaconda\envs\pgam\python.exe -m pgam.testing.verify_config_persistence --mode verify
```

It verifies all of the following across separate Python processes:

- All 15 `AppSettings` keys exist in `app_config`.
- Raw persisted values match expected typed values after reload.
- Task-level advanced adapter configuration survives restart.
- LLM API key survives restart in Windows Credential Manager.
- SMTP password survives restart in Windows Credential Manager.
- Saving from the GUI with blank password fields preserves existing credentials.
- Credential material is absent from SQLite and all files under the data directory.
- Isolated test credentials are deleted after verification.

Latest result:

```json
{
  "settings_keys_covered": true,
  "settings_exact_after_process_restart": true,
  "raw_app_config_exact": true,
  "app_config_keys_exact": true,
  "llm_credential_exact_after_process_restart": true,
  "smtp_credential_exact_after_process_restart": true,
  "blank_gui_password_preserves_credentials": true,
  "task_configuration_exact_after_process_restart": true,
  "credential_material_absent_from_data_files": true,
  "configuration_row_count_is_complete": true,
  "all_passed": true
}
```

The production configuration was also audited after loading the supplied DeepSeek and SMTP credentials. Its 14 SQLite keys are complete, and the production Credential Manager entries now match `token.txt` and `email_password.txt` exactly without printing either value.
