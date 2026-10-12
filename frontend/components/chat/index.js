import {useEffect, useState} from "react";
import ChatContainer from "./container";
import {getChatSettings} from "../../services/chat";
import ChatStore from "./chatStore";
import AuthenticationStore from "../../stores/authentication";

const Chat = () => {
  const auth = AuthenticationStore.useContainer();
  const [enabled, setEnabled] = useState(false);
  useEffect(() => {
    setEnabled(false);
    if (!auth.isAuthenticated || !auth.userId) return;
    let cancelled = false;
    getChatSettings().then(settings => {
      if (!cancelled) setEnabled(settings.chatEnabled === true);
    }).catch(() => {
      if (!cancelled) setEnabled(false);
    });
    return () => { cancelled = true; };
  }, [auth.isAuthenticated, auth.userId]);

  // A global chat feature flag does not grant anonymous conversation access.
  if (!auth.isAuthenticated || !auth.userId || !enabled) return null;
  return <ChatStore.Provider>
    <ChatContainer />
  </ChatStore.Provider>;
};

export default Chat;
