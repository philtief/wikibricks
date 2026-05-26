import { Route, Routes } from "react-router-dom";

import { IndexRoute } from "./routes/IndexRoute";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<IndexRoute />} />
      {/* /queue route added in Task 12 */}
    </Routes>
  );
}
