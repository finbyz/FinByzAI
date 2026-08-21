import { FrappeProvider } from 'frappe-react-sdk'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { OperationsPage, RunDetailPage, RunsPage, WorkflowEditorPage, WorkflowListPage, WorkflowPerformancePage } from './pages/WorkflowPages'
import { EnrollmentPage } from './pages/EnrollmentPage'
import { AttemptExplorerPage } from './pages/AttemptExplorer'
import { TemplateGalleryPage } from './pages/TemplateGalleryPage'
export default function App() {
  return (
    <FrappeProvider socketPort={window.frappe?.boot?.socketio_port ? String(window.frappe.boot.socketio_port) : undefined}>
      <BrowserRouter basename="/workflow">
        <Routes>
          <Route index element={<WorkflowListPage />} />
          <Route path="operations" element={<OperationsPage />} />
          <Route path="templates" element={<TemplateGalleryPage />} />
          <Route path="explorer" element={<AttemptExplorerPage />} />
          <Route path=":workflowId" element={<WorkflowEditorPage />} />
          <Route path=":workflowId/enrollment" element={<EnrollmentPage />} />
          <Route path=":workflowId/runs" element={<RunsPage />} />
          <Route path=":workflowId/performance" element={<WorkflowPerformancePage />} />
          <Route path="runs/:runId" element={<RunDetailPage />} />
        </Routes>
      </BrowserRouter>
    </FrappeProvider>
  )
}
