import { createSlice, createAsyncThunk, PayloadAction } from '@reduxjs/toolkit';
import { getChatHistory, sendMessage } from '../_lib/services/chatService';
import type { ChatMessage, Conversation } from '../_interfaces/api';

interface ChatState {
  conversations: Conversation[];
  activeConversationId: string | null;
  messages: ChatMessage[];
  isTyping: boolean;
  loading: boolean;
  error: string | null;
}

const initialState: ChatState = {
  conversations: [],
  activeConversationId: null,
  messages: [],
  isTyping: false,
  loading: false,
  error: null,
};

export const fetchChatHistory = createAsyncThunk('chat/fetchHistory', async () => {
  return getChatHistory();
});

export const sendChatMessage = createAsyncThunk('chat/sendMessage', async (
  { message, conversationId }: { message: string; conversationId?: string }
) => {
  return sendMessage(message, conversationId);
});

const chatSlice = createSlice({
  name: 'chat',
  initialState,
  reducers: {
    setActiveConversation: (state, action: PayloadAction<string | null>) => {
      state.activeConversationId = action.payload;
    },
    addUserMessage: (state, action: PayloadAction<ChatMessage>) => {
      state.messages.push(action.payload);
    },
    addAssistantMessage: (state, action: PayloadAction<ChatMessage>) => {
      state.messages.push(action.payload);
    },
    appendToLastMessage: (state, action: PayloadAction<string>) => {
      const last = state.messages[state.messages.length - 1];
      if (last && last.role === 'assistant') {
        last.content += action.payload;
      }
    },
    setTyping: (state, action: PayloadAction<boolean>) => {
      state.isTyping = action.payload;
    },
    clearMessages: (state) => {
      state.messages = [];
      state.activeConversationId = null;
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(fetchChatHistory.fulfilled, (state, action) => {
        state.conversations = action.payload;
      })
      .addCase(sendChatMessage.pending, (state) => { state.isTyping = true; })
      .addCase(sendChatMessage.fulfilled, (state, action) => {
        state.isTyping = false;
        state.activeConversationId = action.payload.conversation_id;
        state.messages.push({
          id: `msg-${Date.now()}`,
          role: 'assistant',
          content: action.payload.response,
          timestamp: action.payload.timestamp,
        });
      })
      .addCase(sendChatMessage.rejected, (state, action) => {
        state.isTyping = false;
        state.error = action.error.message || 'Failed to send message';
      });
  },
});

export const {
  setActiveConversation,
  addUserMessage,
  addAssistantMessage,
  appendToLastMessage,
  setTyping,
  clearMessages,
} = chatSlice.actions;
export default chatSlice.reducer;
