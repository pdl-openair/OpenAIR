// eslint 9 flat config for @openair/web.
// Design-frozen rule set: typescript-eslint recommended + react-hooks
// recommended (campaign design section 7.3). Incremental enforcement is
// handled by scripts/gates/diff_gate.py --eslint (only lines added relative
// to the gate base are reported), so legacy findings never block a change.
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist/"] },
  ...tseslint.configs.recommended,
  {
    plugins: {
      "react-hooks": reactHooks,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
    },
  },
);
