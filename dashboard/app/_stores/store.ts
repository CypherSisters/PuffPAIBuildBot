import { configureStore } from "@reduxjs/toolkit";
import darkModeReducer from "./darkModeSlice";
import mainReducer from "./mainSlice";
import systemReducer from "./systemSlice";
import chatReducer from "./chatSlice";
import vaultReducer from "./vaultSlice";
import avatarReducer from "./avatarSlice";
import agentReducer from "./agentSlice";

export const store = configureStore({
  reducer: {
    darkMode: darkModeReducer,
    main: mainReducer,
    system: systemReducer,
    chat: chatReducer,
    vault: vaultReducer,
    avatar: avatarReducer,
    agents: agentReducer,
  },
});

// Infer the `RootState` and `AppDispatch` types from the store itself
export type RootState = ReturnType<typeof store.getState>;
// Inferred type: {posts: PostsState, comments: CommentsState, users: UsersState}
export type AppDispatch = typeof store.dispatch;
