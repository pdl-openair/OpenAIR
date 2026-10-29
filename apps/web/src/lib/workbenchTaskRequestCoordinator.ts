// Runtime implementation lives in .mjs so direct node tests can exercise the same code.
// @ts-ignore The browser build resolves the colocated ESM implementation.
export {
  WorkbenchTaskRequestCoordinator,
  getWorkbenchTaskRequestCoordinator,
  resetWorkbenchTaskRequestCoordinators,
  workbenchTaskRequestCoordinatorKey,
} from "./workbenchTaskRequestCoordinator.mjs";
