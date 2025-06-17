package com.messageforwarder.core;

import com.messageforwarder.model.Message;

public class Forwarder {

    public void forwardMessage(Message message) {
        // 处理消息转发逻辑
        System.out.println("Forwarding message: " + message.getContent());
        // 这里可以添加更多的转发逻辑，例如发送到特定的接收者
    }
}