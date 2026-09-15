// Latin subsets only: the full family ships a hundred CJK slices the app never uses.
import "@fontsource/m-plus-rounded-1c/latin-700.css";
import "@fontsource/m-plus-rounded-1c/latin-800.css";
import "@fontsource/m-plus-rounded-1c/latin-900.css";
import "@fontsource-variable/nunito";
import { createApp } from "vue";
import App from "./App.vue";
import "./style.css";

createApp(App).mount("#app");
