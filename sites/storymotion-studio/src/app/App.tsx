import { BrowserRouter, Navigate, Route, Routes } from "react-router";

import { apiClient, type ApiClient } from "../api/client";
import {
  ProjectListPage,
  type ProjectListApi,
} from "../projects/ProjectListPage";
import {
  ProjectWorkspacePage,
  type ProjectWorkspaceApi,
} from "../projects/ProjectWorkspacePage";
import { SettingsPage } from "../settings/SettingsPage";
import { WorkDetailPage } from "../works/WorkDetailPage";
import { WorksPage } from "../works/WorksPage";
import { AppShell } from "./AppShell";

type AppApi = ProjectListApi &
  Partial<ProjectWorkspaceApi> &
  Partial<Pick<ApiClient, "listWorks" | "getWork" | "getProviderSettings">>;

export function App({ api = apiClient }: { api?: AppApi }) {
  const listWorks = api.listWorks ?? apiClient.listWorks;
  const getWork = api.getWork ?? apiClient.getWork;
  const getProviderSettings = api.getProviderSettings ?? apiClient.getProviderSettings;
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/projects" replace />} />
          <Route path="projects" element={<ProjectListPage api={api} />} />
          <Route path="projects/:id" element={<ProjectWorkspacePage api={api as ProjectWorkspaceApi} />} />
          <Route
            path="projects/:id/stages/:stage"
            element={<ProjectWorkspacePage api={api as ProjectWorkspaceApi} />}
          />
          <Route path="works" element={<WorksPage api={{ listWorks }} />} />
          <Route path="works/:id" element={<WorkDetailPage api={{ getWork }} />} />
          <Route path="settings" element={<SettingsPage api={{ getProviderSettings }} />} />
          <Route path="*" element={<Navigate to="/projects" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
