import { Route, Routes } from "react-router-dom";

import { IndexRoute } from "./routes/IndexRoute";
import { QueueRoute } from "./routes/QueueRoute";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<IndexRoute />} />
      <Route path="/queue" element={<QueueRoute />} />
    </Routes>
  );
}
