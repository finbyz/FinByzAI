# Workflow Builder frontend

React frontend for `finbyzai.workflow_builder`. Frappe serves the application at
`/workflow`; Vite writes versioned assets to
`finbyzai/public/workflow` and refreshes `finbyzai/www/workflow.html`.

Run commands from `apps/finbyzai`:

```bash
yarn test
yarn typecheck
yarn --cwd workflow lint
yarn build
```

The generated public assets and route HTML are deployment artifacts and are not
committed. Build them after installing frontend dependencies on each target
environment.
