package com.messageforwarder;

import com.messageforwarder.core.Forwarder;
import com.messageforwarder.model.Message;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import static org.mockito.Mockito.*;

public class ForwarderTest {

    private Forwarder forwarder;

    @BeforeEach
    public void setUp() {
        forwarder = new Forwarder();
    }

    @Test
    public void testForwardMessage() {
        Message message = new Message();
        message.setId(1);
        message.setContent("Test message");
        message.setTimestamp(System.currentTimeMillis());

        // Mock the behavior of the forwarding logic
        // Assuming there is a method to verify the forwarding
        forwarder.forwardMessage(message);

        // Add assertions to verify the expected behavior
        // For example, you might want to check if the message was sent to the correct recipient
    }
}