package com.messageforwarder.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class Config {

    // Define your beans and application properties here

    @Bean
    public SomeService someService() {
        return new SomeService();
    }

    // Add more configuration as needed
}