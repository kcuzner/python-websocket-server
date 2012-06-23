
var MessageModel = function(type, name, message) {
	
	if (typeof message == "undefined") {
		message = "";
	}
	
	this.type = ko.observable(type);
	this.name = ko.observable(name);
	this.message = ko.observable(message);
	this.isStatus = ko.observable(false);
}

var ChatroomModel = function (name, count) {
	this.name = ko.observable(name);
	this.userCount = ko.observable(count);
}

var overlayState_off = 0;
var overlayState_settings = 1;
var overlayState_error = 2;
var overlayState_waiting = 3;

var ChatroomViewModel = function() {
	var self = this;
	
	/**
	 * Data members
	 */
	this.messages = ko.observableArray(); //chatroom messages
	this.chatrooms = ko.observableArray(); //chatroom listing
	this.chatrooms.find = function(name) {
		for(i in self.chatrooms()) {
			if (self.chatrooms()[i].name() == name) {
				return self.chatrooms()[i];
			}
		}
		return null;
	}
	this.name = ko.observable();
	this.message = ko.observable();
	this.overlayState = ko.observable(overlayState_off);
	this.waitMessage = ko.observable("Connecting");
	this.errorMessage = ko.observable();
	this.currentChatroom = ko.observable(null);
	
	
	/**
	 * Behaviours
	 */
	this.sendMessage = function () {
		if (self.currentChatroom() == null) {
			alert("Please select a chatroom to chat in");
			return;
		}
		if (self.message() == "") {
			//ignore this since we don't have anything to send
			return;
		}
		if (ws.readyState == WebSocket.OPEN) {
			//send the data
			var msg = new Object;
			msg.type = "message";
			msg.message = self.message();
			ws.send(JSON.stringify(msg));
			self.message("");
		}
		else {
			self.errorMessage("Socket not connected");
		}
	}
	
	this.createChatroom = function () {
		var name = prompt("Please enter a name for the chatroom", "");
		if (name == "" || name == null) {
			alert("You must enter a name for the chatroom");
			return;
		}
		if (ws.readyState == WebSocket.OPEN) {
			var msg = new Object;
			msg.type = "create";
			msg.chatroom = name;
			ws.send(JSON.stringify(msg));
		}
		else {
			self.errorMessage("Socket not connected");
		}
	};
	
	this.joinChatroom = function (chatroom) {
		//self.currentChatroom(chatroom);
		if (ws.readyState == WebSocket.OPEN) {
			//send the request
			var msg = new Object;
			msg.type = "join";
			msg.chatroom = chatroom.name();
			ws.send(JSON.stringify(msg));
		}
		else {
			self.errorMessage("Socket not connected");
		}
	};
	
	this.showSettings = function () {
		self.overlayState(overlayState_settings);
	};
	
	this.hideSettings = function () {
		self.overlayState(overlayState_off);
	};
	
	this.processNameKeyDown = function (data, event) {
		if (event.keyCode == 13) {
			//enter pressed. close settings window
			event.target.blur();
			self.hideSettings();
			event.target.focus();
		}
		return true;
	};
	
	this.processMsgKeyDown = function (data, event) {
		if (event.keyCode == 13) {
			event.target.blur();
			self.sendMessage();
		}
		return true;
	};
	
	/**
	 * Event behaviours
	 */
	var n = 0;
	this.messageReceived = function(msg) {
		data = JSON.parse(msg.data); //parse the message into an object
		if (data.type == "query") {
			//this is a query for us...not something that goes on the message queue
			if (data.query == "name") {
				//how sweet...they want to know our name
				alert("Please enter a name in order to chat.");
				self.overlayState(overlayState_settings)
			}
		}
		else if (data.type == "notice") {
			alert(data.notice);
		}
		else if (data.type == "join") {
			//we are to join a room
			self.messages.removeAll();
			var room = self.chatrooms.find(data.chatroom);
			if (room != null) {
				self.currentChatroom(room);
			}
		}
		else if (data.type == "event") {
			//something eventful happened
			switch(data.event.type)
			{
			case "listing":
				self.chatrooms.removeAll();
				self.currentChatroom(null);
				self.messages.removeAll();
				for(i in data.event.chatrooms) {
					self.chatrooms.push(new ChatroomModel(data.event.chatrooms[i][0], data.event.chatrooms[i][1]));
				}
				break;
			case "update":
				var cr = self.chatrooms.find(data.event.data[0]);
				if (cr != null) {
					cr.userCount(data.event.data[1]);
				}
				break;
			case "message":
				//it is a message
				self.messages.push(new MessageModel(data.event.type, data.event.name, data.event.message));
				break;
			case "newuser":
				//a new subscriber!
				self.messages.push(new MessageModel(data.event.type, data.event.name, null));
				break;
			case "logoff":
				//someone unsubscribed
				self.messages.push(new MessageModel(data.event.type, data.event.name, null));
				break;
			case "newchatroom":
				//a new chatroom was created
				self.chatrooms.push(new ChatroomModel(data.event.name, 0));
			}
		}
	}
	
	this.socketClosed = function () {
		self.overlayState(overlayState_error);
		self.errorMessage("Connection closed");
	}
	
	/**
	 * Default behaviours
	 */
	this.name.subscribe( function (value) {
		//we want to tell the socket about our name
		if (ws.readyState == WebSocket.OPEN) {
			//send the data
			var msg = new Object;
			msg.type = "name";
			msg.name = value;
			ws.send(JSON.stringify(msg));
		}
		else {
			self.errorMessage("Socket not connected");
		}
	});
	this.errorMessage.subscribe( function (value) {
		//set this to error mode
		self.overlayState(overlayState_error);
	});
	
	/**
	 * Initial behavior
	 */
	var ws = new WebSocket("ws://" + host + ":" + port + "/demo_chatroom");
	ws.onmessage = this.messageReceived
	ws.onclose = this.socketClosed;
}

ko.bindingHandlers.jqMouseover = {
	init: function (element, valueAccessor) {
		$(element).mouseenter( function () {
			if ($(this).hasClass('ui-state-active')) {
				$(this).removeClass('ui-state-default');
				return;
			}
			$(this).removeClass('ui-state-default').addClass('ui-state-hover');
		}).mouseleave( function () {
			if ($(this).hasClass('ui-state-active')) {
				$(this).removeClass('ui-state-hover');
				return;
			}
			$(this).removeClass('ui-state-hover').addClass('ui-state-default');
		});
		
		$(element).addClass('ui-state-default');
		var value = valueAccessor();
		if (ko.utils.unwrapObservable(value) == true) {
			$(element).addClass('ui-state-active').removeClass('ui-state-default');
		}
	},
	update: function (element, valueAccessor) {
		var value = valueAccessor();
		if (ko.utils.unwrapObservable(value) == true) {
			$(element).addClass('ui-state-active').removeClass('ui-state-default');
		}
		else {
			$(element).addClass('ui-state-default').removeClass('ui-state-active');
		}
	}
};

var view;
$(document).ready( function () {
	view = new ChatroomViewModel();
	ko.applyBindings(view);
});
